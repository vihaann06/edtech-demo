"""Types shared by every pipeline stage: job statuses, progress callbacks, errors."""

from __future__ import annotations

from enum import Enum
from typing import Callable, Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"  # LLM is reading the concept (thinking)
    DESIGNING = "designing"  # LLM is writing / we are validating the storyboard
    GENERATING_CODE = "generating_code"  # LLM is writing Manim code
    RENDERING = "rendering"  # Manim is rendering inside the sandbox container
    REPAIRING = "repairing"  # LLM is fixing code after a failed render
    VALIDATING = "validating"  # ffprobe checks on the MP4
    COMPLETED = "completed"
    FAILED = "failed"


# The five user-facing stages shown in the UI, in order.
STAGE_LABELS: list[str] = [
    "Understanding your concept",
    "Designing your explanation",
    "Creating animations",
    "Rendering your video",
    "Your explanation is ready",
]

STATUS_TO_STAGE_INDEX: dict[JobStatus, int] = {
    JobStatus.QUEUED: 0,
    JobStatus.PLANNING: 0,
    JobStatus.DESIGNING: 1,
    JobStatus.GENERATING_CODE: 2,
    JobStatus.RENDERING: 3,
    JobStatus.REPAIRING: 3,
    JobStatus.VALIDATING: 3,
    JobStatus.COMPLETED: 4,
    # FAILED keeps whatever stage the job had reached; see jobs.py.
}

# Called by pipeline stages to report real progress.
# `detail` is a short human-readable sub-status, e.g. "Writing scene 2 of 4".
ProgressCallback = Callable[[JobStatus, Optional[str]], None]


def noop_progress(status: JobStatus, detail: Optional[str] = None) -> None:
    return None


class PipelineError(Exception):
    """An expected pipeline failure.

    `user_message` is safe to show in the UI. `detail` holds debugging context
    (tracebacks, raw model output) and is only exposed through debug endpoints.
    """

    def __init__(self, user_message: str, *, detail: str | None = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail
