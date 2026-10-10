"""Runs one job end to end: prompt -> storyboard -> rendered, validated MP4.

`run_job` never raises. Every failure ends with `job.fail(...)`: a
`PipelineError` keeps its UI-safe `user_message`; anything else gets a generic
message and the traceback goes to `job.error_detail` (debug endpoint only).

The planner and producer are resolved lazily (and can be injected for tests),
so this module imports cleanly even before those modules exist.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from app.jobs import GENERIC_ERROR, Job
from app.pipeline import offline_demo
from app.pipeline.schema import Storyboard
from app.pipeline.types import JobStatus, PipelineError

log = logging.getLogger(__name__)

Planner = Callable[..., Storyboard]  # plan_lesson(prompt, *, on_progress) -> Storyboard
Producer = Callable[..., Any]  # produce_video(storyboard, job_dir, *, on_progress, code=None) -> ProductionResult


def _default_planner() -> Planner:
    try:
        from app.pipeline.lesson_planner import plan_lesson
    except ImportError as exc:
        raise PipelineError(
            "The lesson planner isn't available on this server.", detail=traceback.format_exc()
        ) from exc
    return plan_lesson


def _default_producer() -> Producer:
    try:
        from app.pipeline.production import produce_video
    except ImportError as exc:
        raise PipelineError(
            "The video renderer isn't available on this server.", detail=traceback.format_exc()
        ) from exc
    return produce_video


def run_job(job: Job, *, planner: Optional[Planner] = None, producer: Optional[Producer] = None) -> None:
    """Plan and produce the video for `job`, reporting progress on the job. Never raises."""
    try:
        _run(job, planner, producer)
    except PipelineError as exc:
        detail = f"{type(exc).__name__}: {exc.user_message}"
        if exc.detail:
            detail += f"\n\n{exc.detail}"
        detail += f"\n\n{traceback.format_exc()}"
        job.fail(exc.user_message, error_detail=detail, attempts=_count_attempts(job.job_dir))
    except BaseException:  # noqa: BLE001 - run_job must never raise
        log.exception("job %s: unexpected error", job.id)
        job.fail(GENERIC_ERROR, error_detail=traceback.format_exc(), attempts=_count_attempts(job.job_dir))
    finally:
        _write_job_record(job)


def _run(job: Job, planner: Optional[Planner], producer: Optional[Producer]) -> None:
    job_dir = job.job_dir
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "prompt.txt").write_text(job.prompt + "\n", encoding="utf-8")

    code: Optional[str] = None
    if job.mode == "offline_demo":
        # Same stages as a live job, but every detail says what is really happening.
        job.update(JobStatus.PLANNING, "Offline demo: loading the pre-written lesson (your prompt is not used)")
        storyboard, code = offline_demo.load_offline_demo()
        job.update(JobStatus.DESIGNING, "Offline demo: using the pre-written storyboard")
    else:
        plan = planner or _default_planner()
        job.update(JobStatus.PLANNING, None)
        storyboard = plan(job.prompt, on_progress=job.update)
        if not isinstance(storyboard, Storyboard):
            raise TypeError(f"planner returned {type(storyboard).__name__}, expected Storyboard")

    (job_dir / "storyboard.json").write_text(storyboard.model_dump_json(indent=2), encoding="utf-8")
    job.set_storyboard(storyboard)

    produce = producer or _default_producer()
    if code is not None:
        job.update(JobStatus.GENERATING_CODE, "Offline demo: using pre-written animation code")
        result = produce(storyboard, job_dir, on_progress=job.update, code=code)
    else:
        result = produce(storyboard, job_dir, on_progress=job.update)

    video_path = Path(result.video_path)
    if not video_path.is_file() or video_path.stat().st_size == 0:
        raise PipelineError("The video file was not produced.", detail=f"missing or empty: {video_path}")
    code_path = Path(result.code_path) if getattr(result, "code_path", None) else None
    video_info = getattr(result, "video_info", None)
    duration = getattr(video_info, "duration_s", None)
    job.complete(
        video_path=video_path,
        code_path=code_path,
        duration=float(duration) if duration is not None else None,
        attempts=getattr(result, "attempts", None),
    )


def _count_attempts(job_dir: Path) -> Optional[int]:
    """Renders attempted so far (from Agent 3's attempts/<n>/ dirs), or None."""
    try:
        n = sum(1 for p in (job_dir / "attempts").iterdir() if p.is_dir())
    except OSError:
        return None
    return n or None


def _write_job_record(job: Job) -> None:
    """Best-effort job.json so a finished job can be inspected after a restart."""
    try:
        snap = job.snapshot()
        if not snap.job_dir.is_dir():
            return
        record = {
            "job_id": snap.id,
            "mode": snap.mode,
            "prompt": snap.prompt,
            "status": snap.status.value,
            "stage_index": snap.stage_index,
            "error": snap.error,
            "error_detail": snap.error_detail,
            "title": snap.storyboard.title if snap.storyboard else None,
            "duration_seconds": snap.duration,
            "attempts": snap.attempts,
            "created_at": snap.created_at,
            "finished_at": snap.finished_at or time.time(),
            "timings": snap.timings(),
            "timeline": snap.timeline,
        }
        (snap.job_dir / "job.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    except Exception:  # pragma: no cover - never let bookkeeping fail a job
        log.debug("job %s: could not write job.json", job.id, exc_info=True)
