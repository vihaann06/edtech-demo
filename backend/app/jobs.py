"""In-memory job store + worker pool.

Jobs live in a dict guarded by a lock; each Job also has its own lock so the
pipeline thread (via progress callbacks) and API threads (via snapshots) never
see a half-updated job. Finished jobs and their directories are removed after
`settings.job_ttl_hours`.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import secrets
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from app.config import get_settings
from app.pipeline.types import STATUS_TO_STAGE_INDEX, JobStatus

if TYPE_CHECKING:  # pragma: no cover
    from app.pipeline.schema import Storyboard

log = logging.getLogger(__name__)

_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_ID_LENGTH = 10
JOB_ID_RE = re.compile(rf"^[a-z0-9]{{{_ID_LENGTH}}}$")

TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED})
GENERIC_ERROR = "Something went wrong while making your video. Please try again."
CLEANUP_INTERVAL_S = 600.0

Runner = Callable[["Job"], None]


def new_job_id() -> str:
    """Short, URL-safe, filesystem-safe id (never starts with '-')."""
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))


@dataclass
class Job:
    id: str
    prompt: str
    mode: str  # "live" | "offline_demo"
    job_dir: Path
    status: JobStatus = JobStatus.QUEUED
    stage_index: int = 0  # monotonic; FAILED keeps the last reached index
    detail: Optional[str] = None
    error: Optional[str] = None  # UI-safe
    error_detail: Optional[str] = None  # debug only (tracebacks, raw output)
    storyboard: Optional["Storyboard"] = None
    video_path: Optional[Path] = None
    code_path: Optional[Path] = None
    duration: Optional[float] = None
    attempts: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # One entry per status change: {"status": str, "at": epoch seconds, "detail": str | None}
    timeline: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.timeline:
            self.timeline.append({"status": self.status.value, "at": self.created_at, "detail": self.detail})

    # --- mutations (all thread-safe) -------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def update(self, status: JobStatus, detail: Optional[str] = None) -> None:
        """ProgressCallback: record real progress reported by a pipeline stage."""
        status = JobStatus(status)
        if status in TERMINAL_STATUSES:
            # Terminal states carry extra data; they must go through complete()/fail().
            log.warning("job %s: ignoring progress callback with terminal status %s", self.id, status.value)
            return
        with self._lock:
            if self.is_terminal:
                return
            now = time.time()
            changed = status != self.status
            self.status = status
            self.detail = detail
            self.stage_index = max(self.stage_index, STATUS_TO_STAGE_INDEX.get(status, self.stage_index))
            self.updated_at = now
            if self.started_at is None and status != JobStatus.QUEUED:
                self.started_at = now
            if changed:
                self.timeline.append({"status": status.value, "at": now, "detail": detail})
        if changed:
            log.info("job %s -> %s%s", self.id, status.value, f" ({detail})" if detail else "")

    def set_storyboard(self, storyboard: "Storyboard") -> None:
        with self._lock:
            self.storyboard = storyboard
            self.updated_at = time.time()

    def complete(
        self, *, video_path: Path, code_path: Optional[Path], duration: Optional[float], attempts: Optional[int]
    ) -> None:
        with self._lock:
            if self.is_terminal:
                return
            now = time.time()
            self.status = JobStatus.COMPLETED
            self.stage_index = STATUS_TO_STAGE_INDEX[JobStatus.COMPLETED]
            self.detail = None
            self.video_path = video_path
            self.code_path = code_path
            self.duration = duration
            self.attempts = attempts
            self.updated_at = self.finished_at = now
            self.timeline.append({"status": JobStatus.COMPLETED.value, "at": now, "detail": None})
            elapsed = now - self.created_at
        log.info("job %s completed in %.1fs (attempts=%s, video=%ss)", self.id, elapsed, attempts, duration)

    def fail(self, error: str, *, error_detail: Optional[str] = None, attempts: Optional[int] = None) -> None:
        with self._lock:
            if self.is_terminal:
                return
            now = time.time()
            last_status = self.status.value
            self.status = JobStatus.FAILED  # stage_index deliberately unchanged
            self.detail = None  # the in-progress sub-status no longer applies; `error` says what happened
            self.error = error
            self.error_detail = error_detail
            if attempts is not None:
                self.attempts = attempts
            self.updated_at = self.finished_at = now
            self.timeline.append({"status": JobStatus.FAILED.value, "at": now, "detail": error})
            elapsed = now - self.created_at
        log.warning("job %s failed during %s after %.1fs: %s", self.id, last_status, elapsed, error)

    # --- reads ------------------------------------------------------------------------

    def snapshot(self) -> "Job":
        """A consistent copy for readers (API handlers)."""
        with self._lock:
            return dataclasses.replace(self, timeline=[dict(e) for e in self.timeline])

    def timings(self) -> dict[str, float]:
        """Seconds spent in each status (summed when a status repeats, e.g. rendering after a repair)."""
        totals: dict[str, float] = {}
        end = self.finished_at if self.finished_at is not None else time.time()
        for entry, nxt in zip(self.timeline, self.timeline[1:] + [None]):
            if entry["status"] in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
                continue
            stop = nxt["at"] if nxt is not None else end
            totals[entry["status"]] = round(totals.get(entry["status"], 0.0) + max(0.0, stop - entry["at"]), 2)
        totals["total"] = round(max(0.0, end - self.created_at), 2)
        return totals


class JobManager:
    def __init__(
        self,
        *,
        runner: Optional[Runner] = None,
        max_workers: Optional[int] = None,
        jobs_dir: Optional[Path] = None,
        ttl_hours: Optional[float] = None,
        cleanup_interval_s: float = CLEANUP_INTERVAL_S,
    ) -> None:
        settings = get_settings()
        self._runner = runner
        self.jobs_dir = Path(jobs_dir) if jobs_dir is not None else settings.jobs_dir
        self.ttl_hours = ttl_hours if ttl_hours is not None else settings.job_ttl_hours
        self.max_workers = max(1, max_workers or settings.max_concurrent_jobs)
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="job-worker")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._cleanup_interval_s = cleanup_interval_s
        self._cleanup_stop = threading.Event()
        self._cleanup_thread: Optional[threading.Thread] = None

    # --- job lifecycle ------------------------------------------------------------------

    def create(self, prompt: str, mode: str = "live") -> Job:
        with self._lock:
            job_id = new_job_id()
            while job_id in self._jobs:  # pragma: no cover - 36^10 ids
                job_id = new_job_id()
            busy = sum(1 for j in self._jobs.values() if not j.is_terminal) >= self.max_workers
            job = Job(
                id=job_id,
                prompt=prompt,
                mode=mode,
                job_dir=self.jobs_dir / job_id,
                detail="Waiting for another video to finish" if busy else None,
            )
            self._jobs[job_id] = job
        return job

    def submit(self, prompt: str, mode: str = "live") -> Job:
        job = self.create(prompt, mode)
        log.info("job %s submitted (%s): %r", job.id, mode, prompt[:80])
        self._executor.submit(self._run, job)
        return job

    def _run(self, job: Job) -> None:
        runner = self._runner
        try:
            if runner is None:
                from app.pipeline.orchestrator import run_job as runner  # lazy: avoids import cycles
            runner(job)
        except BaseException:  # run_job never raises, but never leave a job stuck either
            log.exception("job %s: runner crashed", job.id)
            job.fail(GENERIC_ERROR, error_detail=traceback.format_exc())
        finally:
            if not job.is_terminal:
                job.fail(GENERIC_ERROR, error_detail="runner returned without finishing the job")

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self, job_id: str) -> Optional[Job]:
        job = self.get(job_id)
        return job.snapshot() if job is not None else None

    def update(self, job_id: str, status: JobStatus, detail: Optional[str] = None) -> None:
        job = self.get(job_id)
        if job is not None:
            job.update(status, detail)

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if not j.is_terminal)

    # --- cleanup -------------------------------------------------------------------------

    def cleanup_expired(self, now: Optional[float] = None) -> int:
        """Drop finished jobs older than the TTL, plus stale job directories on disk.

        Only directories that look like job dirs (id-shaped name + prompt.txt) are
        deleted, so a misconfigured JOBS_DIR can't wipe unrelated folders.
        Returns the number of job directories/entries removed.
        """
        now = time.time() if now is None else now
        cutoff = now - self.ttl_hours * 3600.0
        with self._lock:
            expired = [j for j in self._jobs.values() if j.is_terminal and j.created_at < cutoff]
            for job in expired:
                del self._jobs[job.id]
            active_ids = set(self._jobs)

        removed = 0
        for job in expired:
            if job.job_dir.exists():
                shutil.rmtree(job.job_dir, ignore_errors=True)
            removed += 1

        if self.jobs_dir.is_dir():
            for child in self.jobs_dir.iterdir():
                if child.name in active_ids or not child.is_dir() or not JOB_ID_RE.match(child.name):
                    continue
                if not (child / "prompt.txt").exists():
                    continue
                try:
                    mtime = child.stat().st_mtime
                except OSError:
                    continue
                if mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
        if removed:
            log.info("cleanup: removed %d expired job(s)", removed)
        return removed

    def start(self) -> None:
        """Run one cleanup now and keep cleaning every ~10 minutes in a daemon thread."""
        try:
            self.cleanup_expired()
        except Exception:  # pragma: no cover - cleanup must never stop startup
            log.exception("startup cleanup failed")
        if self._cleanup_thread is None:
            self._cleanup_stop.clear()
            self._cleanup_thread = threading.Thread(target=self._cleanup_loop, name="job-cleanup", daemon=True)
            self._cleanup_thread.start()

    def _cleanup_loop(self) -> None:
        while not self._cleanup_stop.wait(self._cleanup_interval_s):
            try:
                self.cleanup_expired()
            except Exception:  # pragma: no cover
                log.exception("periodic cleanup failed")

    def shutdown(self, wait: bool = False) -> None:
        self._cleanup_stop.set()
        self._cleanup_thread = None
        self._executor.shutdown(wait=wait, cancel_futures=True)
