"""Lesson planning: a learner's question -> a validated ~30 s `Storyboard`.

Flow:
1. Check the prompt (non-empty, <= 500 chars) and embed it as data in the user message.
2. Ask the planner model for a `LessonPlanDraft` (structured output; the draft
   schema is deliberately lenient because the API drops length/number limits).
3. Normalize in code: rescale durations to the target total in 0.5 s steps
   (respecting per-scene limits and each caption's reading time), compute
   contiguous start times, set indexes, tidy whitespace and LaTeX delimiters.
4. Validate into `Storyboard`. If that fails, or the model's output was
   unusable, retry ONCE with the errors and the previous JSON as feedback.

The system prompt lives in `prompts/planner_system.md` and is loaded once, so it
is byte-stable across calls (prompt caching).

Manual test (needs ANTHROPIC_API_KEY):
    cd backend && .venv/bin/python -m app.pipeline.lesson_planner "How does binary search work?"
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field, ValidationError

from app import llm
from app.config import get_settings
from app.pipeline.schema import (
    CAPTION_CHARS_PER_SECOND,
    MAX_CAPTION_CHARS,
    MAX_SCENE_SECONDS,
    MAX_SCENES,
    MIN_SCENE_SECONDS,
    MIN_SCENES,
    Storyboard,
)
from app.pipeline.types import JobStatus, PipelineError, ProgressCallback, noop_progress

log = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 500
DURATION_STEP_S = 0.5
TARGET_CAPTION_CPS = 15.0  # what the prompt asks for; the schema's hard limit is CAPTION_CHARS_PER_SECOND
PLANNER_MAX_TOKENS = 32000  # thinking + ~2k tokens of JSON; streaming makes a high cap safe
PURPOSE = "lesson plan"
REQUEST_TAG = "topic_request"
MAX_FEEDBACK_JSON_CHARS = 12000

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PLANNER_SYSTEM_PROMPT = (PROMPTS_DIR / "planner_system.md").read_text(encoding="utf-8")

FAILURE_MESSAGE = "We couldn't design a lesson for that question. Try rephrasing it."
EMPTY_PROMPT_MESSAGE = "Please type a question or concept to explain."
LONG_PROMPT_MESSAGE = f"Please keep your question under {MAX_PROMPT_CHARS} characters."

DETAIL_SKETCHING = "Sketching the storyboard"
DETAIL_REFINING = "Refining the storyboard"


class LessonPlanningError(PipelineError):
    """The question could not be turned into a valid storyboard."""


# --------------------------------------------------------------------------- #
# What the model fills in. Lenient on purpose: strict checks happen in Storyboard.
# --------------------------------------------------------------------------- #


class DraftScene(BaseModel):
    concept: str = Field(description="2-5 word heading for the one thing this scene adds (max 40 characters).")
    duration_seconds: float = Field(
        description="Scene length in seconds, 6-9. All scenes together add up to the requested total."
    )
    caption: str = Field(
        description=(
            "One plain-language, present-tense sentence shown at the bottom of the screen. At most 15 characters "
            "per second of duration_seconds and never more than 120 characters. No LaTeX."
        )
    )
    visual_description: str = Field(
        description="1-3 sentences: what is on screen, with the actual words/numbers and where things sit."
    )
    visual_objects: list[str] = Field(
        description="Short inventory of on-screen elements, with stable names reused across scenes."
    )
    animation_instructions: list[str] = Field(
        description="2-5 ordered beats; each says which object does which motion, where, and roughly how long."
    )
    math_expressions: list[str] = Field(
        description="LaTeX (math mode, no $ delimiters, under 40 characters) of each equation shown; empty if none."
    )
    transition: str = Field(
        description="What carries over into the next scene; for the last scene, how the video ends."
    )


class LessonPlanDraft(BaseModel):
    topic: str = Field(description="The concept in 1-5 words.")
    title: str = Field(description="Plain, specific title in title case, at most 50 characters.")
    learning_objective: str = Field(description="One sentence: the idea the viewer should understand.")
    running_example: str = Field(
        description="One sentence naming the single concrete example, with exact values, that every scene reuses."
    )
    scenes: list[DraftScene] = Field(description="3 or 4 scenes in order: hook, mechanism, payoff.")


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


@dataclass
class _Feedback:
    problems: list[str]
    previous_json: str = ""
    notes: list[str] = field(default_factory=list)


def plan_lesson(prompt: str, *, on_progress: ProgressCallback = noop_progress) -> Storyboard:
    """Turn a natural-language question into a validated ~30 s storyboard.

    Calls on_progress(JobStatus.PLANNING, None) at start and
    on_progress(JobStatus.DESIGNING, <detail>) once the model starts writing the
    storyboard and again if it has to be refined. Raises LessonPlanningError
    (or an llm.LLMError) with a UI-safe user_message.
    """
    request = _check_prompt(prompt)
    settings = get_settings()
    target = float(settings.target_duration_s)

    on_progress(JobStatus.PLANNING, None)

    feedback: Optional[_Feedback] = None
    failures: list[str] = []
    for attempt in (1, 2):
        on_output_start: Optional[Callable[[], None]] = None
        if attempt == 1:
            on_output_start = lambda: on_progress(JobStatus.DESIGNING, DETAIL_SKETCHING)  # noqa: E731
        else:
            on_progress(JobStatus.DESIGNING, DETAIL_REFINING)

        message = build_user_message(request, target_duration=target, feedback=feedback)
        try:
            draft, _ = llm.generate_structured(
                system=PLANNER_SYSTEM_PROMPT,
                messages=message,
                schema=LessonPlanDraft,
                purpose=PURPOSE,
                model=settings.planner_model,
                effort=settings.planner_effort,
                max_tokens=PLANNER_MAX_TOKENS,
                on_output_start=on_output_start,
            )
        except llm.LLMOutputError as exc:
            # Truncated, empty, or schema-mismatched output: worth one more try.
            problem = f"Your previous response could not be used: {exc.user_message}"
            if exc.detail:
                problem += f" ({_shorten(exc.detail, 1500)})"
            failures.append(f"attempt {attempt}: {problem}")
            log.warning("lesson plan attempt %d unusable: %s", attempt, exc.detail or exc.user_message)
            feedback = _Feedback(problems=[problem], previous_json=exc.raw_text)
            continue

        try:
            storyboard = draft_to_storyboard(draft, target_duration=target)
        except ValidationError as exc:
            problems = format_validation_errors(exc)
            failures.append(f"attempt {attempt}: " + "; ".join(problems))
            log.warning("lesson plan attempt %d invalid: %s", attempt, problems)
            feedback = _Feedback(
                problems=problems,
                previous_json=draft.model_dump_json(indent=2),
                notes=_timing_notes(draft, target),
            )
            continue

        log.info(
            "lesson plan ready after %d attempt(s): %r, %d scenes, durations=%s",
            attempt,
            storyboard.title,
            len(storyboard.scenes),
            [s.duration for s in storyboard.scenes],
        )
        return storyboard

    raise LessonPlanningError(FAILURE_MESSAGE, detail="\n".join(failures))


# --------------------------------------------------------------------------- #
# Prompt handling
# --------------------------------------------------------------------------- #

_TAG_PATTERN = re.compile(rf"<\s*/?\s*{REQUEST_TAG}\b[^>]*>", re.IGNORECASE)


def _check_prompt(prompt: object) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise LessonPlanningError(EMPTY_PROMPT_MESSAGE)
    text = prompt.strip()
    if len(text) > MAX_PROMPT_CHARS:
        raise LessonPlanningError(LONG_PROMPT_MESSAGE, detail=f"prompt has {len(text)} characters")
    cleaned = _sanitize_request(text)
    if not cleaned:
        raise LessonPlanningError(EMPTY_PROMPT_MESSAGE)
    return cleaned


def _sanitize_request(text: str) -> str:
    """Make the learner's text safe to embed between our delimiter tags."""
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = _TAG_PATTERN.sub(" ", text)  # nobody gets to close our tag early
    return " ".join(text.split())


def build_user_message(request: str, *, target_duration: float, feedback: Optional[_Feedback] = None) -> str:
    """The single user turn: the request as data, the task, and (on retry) the feedback."""
    total = f"{target_duration:g}"
    parts = [
        f"<{REQUEST_TAG}>\n{request}\n</{REQUEST_TAG}>",
        (
            f"Design the storyboard for a {total}-second explainer that teaches the request above to a "
            f"first-time learner: {MIN_SCENES} or {MAX_SCENES} scenes whose durations add up to exactly "
            f"{total} seconds, each with one caption sentence within its character budget "
            f"(about {TARGET_CAPTION_CPS:g} characters per second, never more than {MAX_CAPTION_CHARS})."
        ),
    ]
    if feedback is not None:
        if feedback.previous_json:
            parts.append(
                "Your previous storyboard for this request:\n"
                f"<previous_attempt>\n{_shorten(feedback.previous_json, MAX_FEEDBACK_JSON_CHARS)}\n</previous_attempt>"
            )
        problem_lines = "\n".join(f"- {p}" for p in feedback.problems)
        parts.append(f"It failed these checks:\n<problems>\n{problem_lines}\n</problems>")
        if feedback.notes:
            note_lines = "\n".join(f"- {n}" for n in feedback.notes)
            parts.append(f"<timing>\n{note_lines}\n</timing>")
        parts.append(
            "Write a corrected storyboard. Fix every listed problem, keep what already works (the idea, the "
            "running example, the arc), and re-check all the rules before answering. Scene lists are 0-based: "
            "scenes[0] is the first scene."
        )
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Normalization -> Storyboard
# --------------------------------------------------------------------------- #


def _clean(text: object) -> str:
    return " ".join(str(text).split())


def _clean_list(items: list[str]) -> list[str]:
    return [c for c in (_clean(item) for item in items) if c]


_MATH_WRAPPERS = (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$"))
_UNESCAPED_DOLLAR = re.compile(r"(?<!\\)\$")


def _clean_math(expr: str) -> str:
    """Strip $...$, $$...$$, \\(...\\), \\[...\\] wrappers and stray unescaped $."""
    text = _clean(expr)
    changed = True
    while changed:
        changed = False
        for left, right in _MATH_WRAPPERS:
            if len(text) > len(left) + len(right) and text.startswith(left) and text.endswith(right):
                text = text[len(left) : -len(right)].strip()
                changed = True
    return _UNESCAPED_DOLLAR.sub("", text).strip()


def _clean_title(title: str) -> str:
    text = _clean(title).strip("\"'“”‘’ ")
    return text[:-1].rstrip() if text.endswith(".") and not text.endswith("..") else text


def _min_units_for_caption(caption: str, step: float) -> int:
    """Smallest number of `step`s that lets this caption be read at the schema's hard limit."""
    seconds = len(caption) / CAPTION_CHARS_PER_SECOND
    return math.ceil(seconds / step - 1e-9)


def apportion_durations(
    raw: list[float],
    *,
    target: float,
    captions: Optional[list[str]] = None,
    step: float = DURATION_STEP_S,
    min_s: float = MIN_SCENE_SECONDS,
    max_s: float = MAX_SCENE_SECONDS,
) -> list[float]:
    """Rescale proposed scene durations so they add up to `target`.

    Works in whole `step`s (0.5 s), keeps each scene within [min_s, max_s], and,
    when feasible, gives every scene at least the time its caption needs at the
    schema's reading speed. The model's proportions are kept as closely as
    possible (largest-remainder apportionment). Deterministic.
    """
    n = len(raw)
    if n == 0:
        return []
    valid = [float(d) for d in raw if isinstance(d, (int, float)) and math.isfinite(d) and d > 0]
    fallback = (sum(valid) / len(valid)) if valid else target / n
    weights = [float(d) if isinstance(d, (int, float)) and math.isfinite(d) and d > 0 else fallback for d in raw]

    total_units = round(target / step)
    lo_default = math.ceil(min_s / step - 1e-9)
    hi = math.floor(max_s / step + 1e-9)
    lows = [lo_default] * n
    if captions is not None and len(captions) == n:
        caption_lows = [min(hi, max(lo_default, _min_units_for_caption(c, step))) for c in captions]
        if sum(caption_lows) <= total_units:
            lows = caption_lows

    weight_sum = sum(weights)
    ideal = [w / weight_sum * total_units for w in weights]
    units = [min(max(math.floor(x), lo), hi) for x, lo in zip(ideal, lows)]

    diff = total_units - sum(units)
    while diff != 0:
        if diff > 0:
            candidates = [i for i in range(n) if units[i] < hi]
            if not candidates:
                break
            pick = max(candidates, key=lambda i: (ideal[i] - units[i], -i))
            units[pick] += 1
            diff -= 1
        else:
            candidates = [i for i in range(n) if units[i] > lows[i]]
            if not candidates:
                break
            pick = min(candidates, key=lambda i: (ideal[i] - units[i], i))
            units[pick] -= 1
            diff += 1
    return [u * step for u in units]


def draft_to_storyboard(draft: LessonPlanDraft, *, target_duration: float) -> Storyboard:
    """Normalize the model's draft and validate it. Raises pydantic.ValidationError."""
    captions = [_clean(scene.caption) for scene in draft.scenes]
    durations = apportion_durations(
        [scene.duration_seconds for scene in draft.scenes], target=target_duration, captions=captions
    )
    scenes = []
    start = 0.0
    for i, (scene, caption, duration) in enumerate(zip(draft.scenes, captions, durations)):
        scenes.append(
            {
                "index": i,
                "concept": _clean(scene.concept),
                "start_time": start,
                "duration": duration,
                "caption": caption,
                "visual_description": _clean(scene.visual_description),
                "animation_instructions": _clean_list(scene.animation_instructions),
                "math_expressions": [m for m in (_clean_math(e) for e in scene.math_expressions) if m],
                "visual_objects": _clean_list(scene.visual_objects),
                "transition": _clean(scene.transition),
            }
        )
        start += duration
    return Storyboard.model_validate(
        {
            "topic": _clean(draft.topic),
            "title": _clean_title(draft.title),
            "target_duration": target_duration,
            "learning_objective": _clean(draft.learning_objective),
            "running_example": _clean(draft.running_example),
            "scenes": scenes,
        }
    )


# --------------------------------------------------------------------------- #
# Feedback helpers
# --------------------------------------------------------------------------- #


def _loc_to_path(loc: tuple) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path


def format_validation_errors(exc: ValidationError) -> list[str]:
    """Turn a Storyboard ValidationError into short, model-readable problem lines."""
    lines = []
    for err in exc.errors(include_url=False):
        msg = str(err.get("msg", "invalid value"))
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        path = _loc_to_path(tuple(err.get("loc", ())))
        value = err.get("input")
        if path.endswith(("caption", "title", "concept")) and isinstance(value, str):
            msg += f" (yours has {len(value)}: {_shorten(value, 140)!r})"
        lines.append(f"{path}: {msg}" if path else msg)
    return lines or [str(exc)]


def _timing_notes(draft: LessonPlanDraft, target: float) -> list[str]:
    """After rescaling, how long each scene really is and what its caption may hold."""
    captions = [_clean(s.caption) for s in draft.scenes]
    if not (MIN_SCENES <= len(draft.scenes) <= MAX_SCENES):
        return [f"The storyboard needs {MIN_SCENES} or {MAX_SCENES} scenes; yours has {len(draft.scenes)}."]
    durations = apportion_durations([s.duration_seconds for s in draft.scenes], target=target, captions=captions)
    notes = [f"Scene durations are rescaled to add up to {target:g} s; yours became {', '.join(f'{d:g} s' for d in durations)}."]
    for i, (caption, duration) in enumerate(zip(captions, durations)):
        limit = min(MAX_CAPTION_CHARS, int(duration * CAPTION_CHARS_PER_SECOND))
        aim = min(MAX_CAPTION_CHARS, int(duration * TARGET_CAPTION_CPS))
        status = "OK" if len(caption) <= limit else "TOO LONG"
        notes.append(
            f"scenes[{i}]: {duration:g} s, caption {len(caption)} characters (hard limit {limit}, aim for {aim} or fewer): {status}"
        )
    return notes


def _shorten(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 15] + " ...[truncated]"


# --------------------------------------------------------------------------- #
# CLI: python -m app.pipeline.lesson_planner "your question"
# --------------------------------------------------------------------------- #


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Plan a ~30 s lesson storyboard for a question (needs ANTHROPIC_API_KEY).")
    parser.add_argument("question", help="the question or concept to explain")
    args = parser.parse_args(argv)

    def progress(status: JobStatus, detail: Optional[str]) -> None:
        print(f"[{status.value}] {detail or ''}".rstrip(), file=sys.stderr, flush=True)

    try:
        storyboard = plan_lesson(args.question, on_progress=progress)
    except PipelineError as exc:
        print(f"error: {exc.user_message}", file=sys.stderr)
        if exc.detail:
            print(exc.detail, file=sys.stderr)
        return 2 if isinstance(exc, llm.LLMNotConfiguredError) else 1
    print(json.dumps(storyboard.model_dump(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    sys.exit(_main(sys.argv[1:]))
