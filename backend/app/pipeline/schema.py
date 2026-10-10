"""The validated storyboard: the contract between lesson planning and Manim generation.

The lesson planner produces a `Storyboard`; the Manim code generator consumes it.
Field names here are relied on by both sides - extend, don't rename.

Text conventions the code generator can rely on:
* `caption` is one line of plain text (no LaTeX, no backslashes). It may contain
  any other printable character, including `$`, `%`, `&`, `<`, so render it with
  Manim `Text`, never `Tex`/`MathTex`/`MarkupText`.
* every `math_expressions` entry is a short LaTeX math-mode string without `$`
  delimiters and with balanced braces, suitable for `MathTex(...)`.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

# A comfortable reading speed for on-screen captions, in characters per second.
CAPTION_CHARS_PER_SECOND = 17.0
MIN_SCENES, MAX_SCENES = 3, 4
MIN_SCENE_SECONDS, MAX_SCENE_SECONDS = 5.0, 12.0
MAX_CAPTION_CHARS = 120
MAX_TITLE_CHARS = 60
MAX_ANIMATION_BEATS = 6
MAX_MATH_EXPRESSIONS = 3
MAX_MATH_CHARS = 100
MAX_VISUAL_OBJECTS = 8

_UNESCAPED_DOLLAR = re.compile(r"(?<!\\)\$")


def _collapse(value: object) -> object:
    """Collapse runs of whitespace (including newlines) into single spaces."""
    return " ".join(value.split()) if isinstance(value, str) else value


def _braces_balanced(expr: str) -> bool:
    depth = 0
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "\\":
            i += 2  # skip the escaped character, e.g. \{ or \}
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


class StoryboardScene(BaseModel):
    index: int = Field(ge=0, description="0-based position of the scene in the lesson.")
    concept: str = Field(min_length=2, max_length=48, description="Short heading for the idea this scene teaches.")
    start_time: float = Field(ge=0, description="Seconds from the start of the video.")
    duration: float = Field(ge=MIN_SCENE_SECONDS, le=MAX_SCENE_SECONDS, description="Scene length in seconds.")
    caption: str = Field(
        min_length=10,
        max_length=MAX_CAPTION_CHARS,
        description="The on-screen caption: one plain-language sentence a first-time learner can read in time.",
    )
    visual_description: str = Field(min_length=3, description="What the viewer sees on screen during this scene.")
    animation_instructions: list[str] = Field(
        min_length=1,
        max_length=MAX_ANIMATION_BEATS,
        description="Ordered animation beats, each one concrete enough to implement in Manim.",
    )
    math_expressions: list[str] = Field(
        default_factory=list,
        max_length=MAX_MATH_EXPRESSIONS,
        description="LaTeX expressions shown in this scene (no $ delimiters). May be empty.",
    )
    visual_objects: list[str] = Field(
        default_factory=list,
        max_length=MAX_VISUAL_OBJECTS,
        description="Suggested on-screen objects, e.g. 'token boxes', 'weighted arrows'.",
    )
    transition: str = Field(description="How this scene hands off to the next one (or ends the video).")

    @property
    def end_time(self) -> float:
        return self.start_time + self.duration

    @field_validator("concept", "caption", "visual_description", "transition", mode="before")
    @classmethod
    def _single_line(cls, value: object) -> object:
        return _collapse(value)

    @field_validator("caption")
    @classmethod
    def _caption_plain_text(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("caption must be plain text: no LaTeX or backslashes (write math in words or Unicode)")
        return value

    @field_validator("animation_instructions", "visual_objects", mode="before")
    @classmethod
    def _strip_items(cls, value: object) -> object:
        if isinstance(value, list):
            return [_collapse(item) for item in value]
        return value

    @field_validator("animation_instructions")
    @classmethod
    def _beats_not_blank(cls, value: list[str]) -> list[str]:
        if any(not beat for beat in value):
            raise ValueError("animation beats must not be empty")
        return value

    @field_validator("math_expressions")
    @classmethod
    def _math_is_mathtex_ready(cls, value: list[str]) -> list[str]:
        cleaned = []
        for expr in value:
            expr = " ".join(expr.split())
            if not expr:
                raise ValueError("math expressions must not be empty")
            if len(expr) > MAX_MATH_CHARS:
                raise ValueError(f"math expression is {len(expr)} characters; keep it under {MAX_MATH_CHARS}: {expr!r}")
            if _UNESCAPED_DOLLAR.search(expr):
                raise ValueError(f"math expression must not contain $ delimiters: {expr!r}")
            if not _braces_balanced(expr):
                raise ValueError(f"math expression has unbalanced braces: {expr!r}")
            cleaned.append(expr)
        return cleaned


class Storyboard(BaseModel):
    topic: str = Field(min_length=2, max_length=80, description="The user's concept, restated as a short topic name.")
    title: str = Field(min_length=3, max_length=MAX_TITLE_CHARS, description="Lesson title shown in the video and UI.")
    target_duration: float = Field(default=30.0, description="Planned total duration in seconds.")
    learning_objective: str = Field(
        min_length=10, max_length=240, description="One sentence: what the viewer should understand afterwards."
    )
    running_example: str = Field(
        default="",
        max_length=300,
        description=(
            "The one concrete example (with its exact values) that every scene reuses, e.g. "
            "'the array [3, 8, 15, 21, 42] and the target 21'. Empty if not provided."
        ),
    )
    scenes: list[StoryboardScene] = Field(min_length=MIN_SCENES, max_length=MAX_SCENES)

    @property
    def total_duration(self) -> float:
        return sum(scene.duration for scene in self.scenes)

    @field_validator("topic", "title", "learning_objective", "running_example", mode="before")
    @classmethod
    def _single_line(cls, value: object) -> object:
        return _collapse(value)

    @model_validator(mode="after")
    def _check_timeline(self) -> "Storyboard":
        expected_start = 0.0
        for i, scene in enumerate(self.scenes):
            if scene.index != i:
                raise ValueError(f"scenes[{i}] has index {scene.index}; indexes must be 0..{len(self.scenes) - 1}")
            if abs(scene.start_time - expected_start) > 0.05:
                raise ValueError(
                    f"scenes[{i}] starts at {scene.start_time:.2f}s but the previous scene ends at {expected_start:.2f}s"
                )
            max_chars = scene.duration * CAPTION_CHARS_PER_SECOND
            if len(scene.caption) > max_chars:
                raise ValueError(
                    f"scenes[{i}] caption has {len(scene.caption)} characters, too long to read in "
                    f"{scene.duration:.1f}s (max {int(max_chars)})"
                )
            expected_start = scene.end_time
        if abs(self.total_duration - self.target_duration) > 3.0:
            raise ValueError(
                f"scene durations add up to {self.total_duration:.1f}s; they must total "
                f"{self.target_duration:.0f}s (+/- 3s)"
            )
        return self
