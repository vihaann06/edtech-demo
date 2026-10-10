"""HTTP request/response models (see docs/INTERFACES.md, "HTTP API")."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import BaseModel, field_validator
from pydantic_core import PydanticCustomError

from app.pipeline.types import STAGE_LABELS, JobStatus

if TYPE_CHECKING:  # pragma: no cover
    from app.jobs import Job

MAX_PROMPT_CHARS = 500

JobMode = Literal["live", "offline_demo"]


class GenerateRequest(BaseModel):
    prompt: str
    mode: JobMode = "live"

    @field_validator("prompt")
    @classmethod
    def _clean_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise PydanticCustomError("prompt_empty", "Please enter a question or concept to explain.")
        if len(value) > MAX_PROMPT_CHARS:
            raise PydanticCustomError(
                "prompt_too_long",
                "Your prompt is {length} characters long; please keep it under {limit} characters.",
                {"length": len(value), "limit": MAX_PROMPT_CHARS},
            )
        return value


class GenerateResponse(BaseModel):
    job_id: str


class SceneSummary(BaseModel):
    index: int
    concept: str
    caption: str
    start_time: float
    duration: float


class StoryboardSummary(BaseModel):
    title: str
    learning_objective: str
    scenes: list[SceneSummary]


class JobResponse(BaseModel):
    job_id: str
    prompt: str
    mode: JobMode
    status: JobStatus
    stage_index: int
    progress_stage: str
    stages: list[str]
    detail: Optional[str] = None
    error: Optional[str] = None
    title: Optional[str] = None
    storyboard: Optional[StoryboardSummary] = None
    video_url: Optional[str] = None
    download_url: Optional[str] = None
    duration_seconds: Optional[float] = None
    attempts: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    elapsed_seconds: float

    @classmethod
    def from_job(cls, job: "Job") -> "JobResponse":
        """Build the response from a job *snapshot* (see JobManager.snapshot)."""
        storyboard = None
        if job.storyboard is not None:
            sb = job.storyboard
            storyboard = StoryboardSummary(
                title=sb.title,
                learning_objective=sb.learning_objective,
                scenes=[
                    SceneSummary(
                        index=s.index,
                        concept=s.concept,
                        caption=s.caption,
                        start_time=s.start_time,
                        duration=s.duration,
                    )
                    for s in sb.scenes
                ],
            )
        completed = job.status == JobStatus.COMPLETED
        end = job.finished_at if job.finished_at is not None else time.time()
        return cls(
            job_id=job.id,
            prompt=job.prompt,
            mode=job.mode,
            status=job.status,
            stage_index=job.stage_index,
            progress_stage=STAGE_LABELS[job.stage_index],
            stages=list(STAGE_LABELS),
            detail=job.detail,
            error=job.error,
            title=job.storyboard.title if job.storyboard is not None else None,
            storyboard=storyboard,
            video_url=f"/api/jobs/{job.id}/video" if completed else None,
            download_url=f"/api/jobs/{job.id}/video?download=1" if completed else None,
            duration_seconds=round(job.duration, 2) if job.duration is not None else None,
            attempts=job.attempts,
            created_at=_utc(job.created_at),
            updated_at=_utc(job.updated_at),
            elapsed_seconds=round(max(0.0, end - job.created_at), 1),
        )


class HealthResponse(BaseModel):
    ok: bool
    anthropic_configured: bool
    planner_model: str
    codegen_model: str
    sandbox: dict[str, Any]
    offline_demo_available: bool


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0)
