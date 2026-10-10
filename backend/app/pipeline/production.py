"""Storyboard -> validated MP4: generate code, render in the sandbox, repair and retry, validate.

    produce_video(storyboard, job_dir, on_progress=..., code=None) -> ProductionResult

Per-attempt records (attempt dir, stage, error excerpt, timeline, timings) are written to
<job_dir>/attempts.json after every attempt so the debug endpoint can show progress.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings
from app.llm import LLMNotConfiguredError
from app.pipeline import manim_generator, renderer, validator
from app.pipeline.manim_generator import CodeGenerationError
from app.pipeline.renderer import ProductionError, RenderResult, SandboxUnavailableError
from app.pipeline.schema import Storyboard
from app.pipeline.types import JobStatus, ProgressCallback, noop_progress
from app.pipeline.validator import VideoInfo, VideoValidationError

log = logging.getLogger(__name__)

ATTEMPTS_FILE = "attempts.json"
FINAL_VIDEO = "final.mp4"
FINAL_CODE = "scene.py"
TIMING_TOLERANCE_S = 0.1  # each scene must end within this of its storyboard end time
_NOT_REPAIRABLE = {"codec", "pix_fmt", "decode", "size", "probe", "no_video"}

__all__ = ["ProductionError", "ProductionResult", "AttemptRecord", "produce_video", "timing_problems",
           "render_error_excerpt"]


@dataclass
class AttemptRecord:
    attempt: int
    dir: str
    stage: str  # "static_check" | "render" | "timeout" | "timing" | "validation" | "ok"
    ok: bool
    error: Optional[str] = None
    code_source: str = "generated"  # "generated" | "repaired" | "supplied"
    started_at: str = ""
    wall_time_s: float = 0.0
    render_time_s: Optional[float] = None
    exit_code: Optional[int] = None
    timeline: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    layout_fixes: int = 0
    animations: int = 0
    video_info: Optional[dict[str, Any]] = None
    notes: list[str] = field(default_factory=list)


@dataclass
class ProductionResult:
    video_path: Path  # <job_dir>/final.mp4
    code_path: Path  # <job_dir>/scene.py
    video_info: VideoInfo
    attempts: int  # code versions tried (1 = the first code rendered and validated)
    attempt_records: list[AttemptRecord] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Error excerpts for the repair prompt
# --------------------------------------------------------------------------------------

_LINE_RE = re.compile(r'File "(?:/work/)?scene\.py", line (\d+)')


def _code_context(code: str, line_no: int, radius: int = 3) -> str:
    lines = code.splitlines()
    lo, hi = max(1, line_no - radius), min(len(lines), line_no + radius)
    out = []
    for i in range(lo, hi + 1):
        marker = ">>" if i == line_no else "  "
        out.append(f"{marker} {i:4d} | {lines[i - 1]}")
    return "\n".join(out)


def render_error_excerpt(result: RenderResult, code: str, timeout_s: float | None = None) -> str:
    """A focused description of why a render failed: traceback tail + the offending source lines."""
    if result.timed_out:
        return (
            f"Rendering exceeded the {timeout_s or get_settings().render_timeout_s:.0f} s time limit and was stopped "
            f"(after {result.animations_rendered} animations). Make the scene cheaper to render: fewer mobjects, "
            "no always_redraw on large groups, no huge loops of tiny animations, fewer MathTex objects."
        )
    tb = (result.traceback or "").strip()
    tb_lines = tb.splitlines()
    if len(tb_lines) > 40:
        tb = "\n".join(["(traceback truncated)"] + tb_lines[-40:])
    parts = [tb or "The renderer exited without producing a video."]
    line_nos = [int(m.group(1)) for m in _LINE_RE.finditer(result.traceback or "")]
    if line_nos:
        parts.append(f"Your code around line {line_nos[-1]}:\n{_code_context(code, line_nos[-1])}")
    if result.exit_code not in (0, None) and not tb:
        tail = "\n".join((result.log_text or "").splitlines()[-30:])
        parts.append(f"Last log lines:\n{tail}")
    return "\n\n".join(parts)


def timing_problems(result: RenderResult, storyboard: Storyboard) -> list[str]:
    """Compare the EXPLAINER_TIMELINE lines against the storyboard."""
    problems: list[str] = []
    by_index = {int(e.get("index", -1)): e for e in result.timeline}
    end = 0.0
    for sc in storyboard.scenes:
        e = by_index.get(sc.index)
        end += sc.duration
        if e is None:
            problems.append(f"scene_block(index={sc.index}) never finished, so scene {sc.index} is missing from the video.")
            continue
        if abs(float(e.get("end", 0.0)) - end) > TIMING_TOLERANCE_S:
            problems.append(
                f"scene {sc.index} ended at {float(e.get('end', 0)):.2f}s instead of {end:.2f}s "
                f"(animations before it overran by {float(e.get('end', 0)) - end:+.2f}s)."
            )
    if result.summary:
        total = float(result.summary.get("duration", 0.0))
        if abs(total - storyboard.total_duration) > TIMING_TOLERANCE_S:
            problems.append(
                f"The video is {total:.2f}s long but the storyboard totals {storyboard.total_duration:.2f}s "
                f"({float(result.summary.get('outside_block_seconds', 0)):.2f}s of animation ran outside scene blocks)."
            )
    return problems


# --------------------------------------------------------------------------------------
# Progress from the render log
# --------------------------------------------------------------------------------------


class _RenderProgress:
    _ANIM = re.compile(r"Animation (\d+) : Partial movie file written")
    _BLOCK = re.compile(r'^EXPLAINER_BLOCK_START (\{.*\})')

    def __init__(self, on_progress: ProgressCallback, total_scenes: int) -> None:
        self.on_progress = on_progress
        self.total = total_scenes
        self.scene: Optional[int] = None
        self.last: Optional[str] = None

    def emit(self, detail: str) -> None:
        if detail != self.last:
            self.last = detail
            try:
                self.on_progress(JobStatus.RENDERING, detail)
            except Exception:  # pragma: no cover
                log.exception("progress callback failed")

    def __call__(self, line: str) -> None:
        m = self._BLOCK.match(line.strip())
        if m:
            try:
                self.scene = int(json.loads(m.group(1)).get("index", 0))
            except (ValueError, json.JSONDecodeError):
                pass
            return
        m = self._ANIM.search(line)
        if m:
            n = int(m.group(1)) + 1
            suffix = f" (scene {self.scene + 1} of {self.total})" if self.scene is not None and self.total else ""
            self.emit(f"Rendering animation {n}{suffix}")
            return
        if "Combining to Movie file" in line:
            self.emit("Assembling the video")


# --------------------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_attempts(job_dir: Path, records: list[AttemptRecord]) -> None:
    try:
        (job_dir / ATTEMPTS_FILE).write_text(json.dumps([asdict(r) for r in records], indent=2, default=str),
                                             encoding="utf-8")
    except OSError:  # pragma: no cover
        log.exception("could not write attempts.json")


def _repair(storyboard: Storyboard, code: str, error: str, attempt: int, rec: AttemptRecord,
            on_progress: ProgressCallback, max_attempts: int, last_error: str) -> str:
    """One logical repair (retried once if the answer contains no code)."""
    for tries in (1, 2):
        try:
            return manim_generator.repair_manim_code(
                storyboard, code, error, attempt, stage=rec.stage, on_progress=on_progress, max_attempts=max_attempts,
            )
        except CodeGenerationError as exc:
            rec.notes.append(f"repair returned no code (try {tries}): {exc.detail}")
        except LLMNotConfiguredError as exc:
            raise ProductionError(
                "We couldn't render this animation, and AI repair isn't available.",
                detail=f"{last_error}\n\nrepair unavailable: {exc.user_message}",
            ) from exc
    raise ProductionError(
        f"We couldn't render this animation after {attempt} attempt{'s' if attempt != 1 else ''}.",
        detail=f"{last_error}\n\nThe repair step returned no code twice.",
    )


def produce_video(
    storyboard: Storyboard,
    job_dir: Path,
    *,
    on_progress: ProgressCallback = noop_progress,
    code: str | None = None,
    render_kwargs: dict[str, Any] | None = None,
) -> ProductionResult:
    """Generate (unless `code` is given), render, repair + retry, validate, and copy to job_dir.

    `render_kwargs` (optional, e.g. {"width": 1280, "height": 720}) overrides render settings;
    the validator then expects the same size.
    """
    s = get_settings()
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    render_kwargs = dict(render_kwargs or {})
    expected_size = (render_kwargs.get("width", s.render_width), render_kwargs.get("height", s.render_height))
    max_attempts = 1 + max(0, s.max_repair_attempts)
    storyboard_json = storyboard.model_dump_json(indent=2)
    records: list[AttemptRecord] = []

    # Fail fast (before spending tokens) if the sandbox is not usable.
    try:
        renderer.ensure_sandbox()
    except SandboxUnavailableError as exc:
        raise ProductionError(exc.user_message, detail=exc.detail) from exc

    source = "supplied"
    if code is None:
        on_progress(JobStatus.GENERATING_CODE, "Planning the animations")
        try:
            code = manim_generator.generate_manim_code(storyboard, on_progress=on_progress)
        except CodeGenerationError as first:
            log.warning("code generation returned no code; retrying once: %s", first.detail)
            on_progress(JobStatus.GENERATING_CODE, "Retrying the animation code")
            code = manim_generator.generate_manim_code(storyboard, on_progress=on_progress)
        source = "generated"

    last_error = "unknown error"
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        started = time.monotonic()
        adir = job_dir / "attempts" / str(attempt)
        adir.mkdir(parents=True, exist_ok=True)
        (adir / FINAL_CODE).write_text(code, encoding="utf-8")
        rec = AttemptRecord(attempt=attempt, dir=str(adir), stage="static_check", ok=False, code_source=source,
                            started_at=_now())
        records.append(rec)

        stage, error, repairable = None, None, True
        problems = manim_generator.static_check(code, storyboard)
        result: Optional[RenderResult] = None
        info: Optional[VideoInfo] = None
        if problems:
            stage = "static_check"
            error = "The code was rejected before rendering:\n" + "\n".join(f"- {p}" for p in problems)
        else:
            rec.stage = "render"
            _write_attempts(job_dir, records)
            on_progress(JobStatus.RENDERING, "Starting the renderer")
            progress = _RenderProgress(on_progress, len(storyboard.scenes))
            try:
                result = renderer.render(
                    code, adir, timeout_s=s.render_timeout_s, on_log=progress, storyboard_json=storyboard_json,
                    on_queue=lambda: on_progress(JobStatus.RENDERING, "Waiting for the renderer"),
                    **render_kwargs,
                )
            except SandboxUnavailableError as exc:
                rec.error = exc.detail
                _write_attempts(job_dir, records)
                raise ProductionError(exc.user_message, detail=exc.detail) from exc
            rec.render_time_s = result.wall_time_s
            rec.exit_code = result.exit_code
            rec.timeline = result.timeline
            rec.warnings = result.warnings
            rec.layout_fixes = len(result.layout_fixes)
            rec.animations = result.animations_rendered
            if not result.success:
                stage = "timeout" if result.timed_out else "render"
                error = render_error_excerpt(result, code, s.render_timeout_s)
            else:
                on_progress(JobStatus.VALIDATING, "Checking the video")
                try:
                    info = validator.validate_video(
                        result.video_path, expected_duration=s.target_duration_s, tolerance=s.duration_tolerance_s,
                        expected_size=expected_size,
                    )
                    rec.video_info = info.to_dict()
                except VideoValidationError as exc:
                    stage = "validation"
                    error = f"{exc.user_message} ({exc.detail})"
                    repairable = exc.kind not in _NOT_REPAIRABLE
                if stage is None:
                    tprobs = timing_problems(result, storyboard)
                    if tprobs:
                        if attempt < max_attempts:
                            stage = "timing"
                            error = "The video's timing does not match the storyboard:\n" + "\n".join(
                                f"- {p}" for p in tprobs)
                        else:
                            rec.notes.append("accepted with timing deviations: " + "; ".join(tprobs))

        rec.wall_time_s = round(time.monotonic() - started, 2)
        if stage is None and result is not None and info is not None:
            rec.stage, rec.ok = "ok", True
            _write_attempts(job_dir, records)
            final_video = job_dir / FINAL_VIDEO
            final_code = job_dir / FINAL_CODE
            shutil.copyfile(result.video_path, final_video)
            final_code.write_text(code, encoding="utf-8")
            info.size_bytes = final_video.stat().st_size
            return ProductionResult(
                video_path=final_video, code_path=final_code, video_info=info, attempts=attempt,
                attempt_records=records, timeline=result.timeline,
            )

        rec.stage, rec.error = stage or "render", error
        last_error = f"[attempt {attempt}, {rec.stage}] {error}"
        _write_attempts(job_dir, records)
        log.warning("production attempt %d failed at %s: %s", attempt, rec.stage, (error or "")[:500])
        if not repairable:
            raise ProductionError("The rendered video failed a quality check.", detail=last_error)
        if attempt >= max_attempts:
            break
        on_progress(JobStatus.REPAIRING, f"Fixing an animation issue (attempt {attempt + 1} of {max_attempts})")
        code = _repair(storyboard, code, error or "", attempt, rec, on_progress, max_attempts, last_error)
        source = "repaired"
        _write_attempts(job_dir, records)

    raise ProductionError(
        f"We couldn't render this animation after {attempt} attempt{'s' if attempt != 1 else ''}.",
        detail=last_error,
    )
