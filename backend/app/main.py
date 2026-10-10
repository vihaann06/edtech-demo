"""FastAPI app: the HTTP API in docs/INTERFACES.md.

Run: cd backend && .venv/bin/uvicorn app.main:app --port 8000
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from app.config import get_settings
from app.jobs import JOB_ID_RE, Job, JobManager
from app.models import GenerateRequest, GenerateResponse, HealthResponse, JobResponse
from app.pipeline import offline_demo
from app.pipeline.types import JobStatus

log = logging.getLogger("app")

LOG_TAIL_CHARS = 4000


# --- logging ------------------------------------------------------------------------------


class _QuietPollFilter(logging.Filter):
    """Drop uvicorn access-log lines for successful job polls (the UI polls every second)."""

    _poll = re.compile(r"^/api/jobs/[^/?]+$")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            method, path, status = args[1], str(args[2]), args[4]
            if method == "GET" and status == 200 and self._poll.match(path):
                return False
        return True


def configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    logging.getLogger("app").setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _QuietPollFilter) for f in access.filters):
        access.addFilter(_QuietPollFilter())


# --- helpers -----------------------------------------------------------------------------


def _jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def _get_job(request: Request, job_id: str) -> Job:
    """Consistent snapshot of a job, or 404."""
    job = _jobs(request).snapshot(job_id) if JOB_ID_RE.match(job_id) else None
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found. It may have expired.")
    return job


def slugify(text: str, *, max_len: int = 60, fallback: str = "explainer-video") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = slug[:max_len].rstrip("-")
    return slug or fallback


def _tail(path: Path, max_chars: int = LOG_TAIL_CHARS) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_chars * 2))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:]


def _attempt_dirs(job_dir: Path) -> list[Path]:
    root = job_dir / "attempts"
    if not root.is_dir():
        return []
    dirs = [p for p in root.iterdir() if p.is_dir()]
    return sorted(dirs, key=lambda p: (0, int(p.name)) if p.name.isdigit() else (1, p.name))


def _validation_message(exc: RequestValidationError) -> str:
    messages = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ()) if part != "body"]
        msg = err.get("msg", "Invalid value")
        if err.get("type") in ("prompt_empty", "prompt_too_long"):
            messages.append(msg)
        elif loc:
            messages.append(f"{'.'.join(loc)}: {msg}")
        else:
            messages.append(msg)
    return "; ".join(messages) or "Invalid request."


def _sandbox_status() -> dict[str, Any]:
    settings = get_settings()
    try:
        from app.pipeline.renderer import sandbox_status
    except ImportError as exc:
        return {
            "docker_available": False,
            "image_present": False,
            "image": settings.renderer_image,
            "error": f"renderer module not available: {exc}",
        }
    try:
        status = sandbox_status()
        return dict(status) if isinstance(status, dict) else {"error": f"unexpected sandbox_status(): {status!r}"}
    except Exception as exc:  # health must never 500
        log.exception("sandbox_status() failed")
        return {"docker_available": False, "image_present": False, "image": settings.renderer_image, "error": str(exc)}


# --- app -----------------------------------------------------------------------------------


def create_app(*, job_manager: Optional[JobManager] = None) -> FastAPI:
    configure_logging()
    settings = get_settings()
    manager = job_manager if job_manager is not None else JobManager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.start()
        log.info(
            "Explainer backend ready: jobs_dir=%s anthropic_configured=%s offline_demo=%s workers=%d",
            manager.jobs_dir,
            get_settings().anthropic_configured,
            offline_demo.is_available(),
            manager.max_workers,
        )
        try:
            yield
        finally:
            manager.shutdown(wait=False)

    app = FastAPI(title="AI Explainer Video API", version="0.1.0", lifespan=lifespan)
    app.state.jobs = manager

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "HEAD", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "Content-Range", "Accept-Ranges", "Content-Length"],
    )

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _validation_message(exc)})

    @app.post("/api/generate", response_model=GenerateResponse)
    def generate(body: GenerateRequest, request: Request) -> GenerateResponse:
        settings = get_settings()
        if body.mode == "live" and not settings.anthropic_configured:
            raise HTTPException(
                status_code=503,
                detail=(
                    "AI generation is not configured on this server. Set ANTHROPIC_API_KEY in the .env file "
                    "and restart the backend"
                    + (", or try the offline demo." if offline_demo.is_available() else ".")
                ),
            )
        if body.mode == "offline_demo" and not settings.allow_offline_demo:
            raise HTTPException(
                status_code=400, detail="The offline demo is disabled on this server (ALLOW_OFFLINE_DEMO=false)."
            )
        job = _jobs(request).submit(body.prompt, body.mode)
        return GenerateResponse(job_id=job.id)

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str, request: Request) -> JobResponse:
        return JobResponse.from_job(_get_job(request, job_id))

    @app.api_route("/api/jobs/{job_id}/video", methods=["GET", "HEAD"])
    def get_video(job_id: str, request: Request, download: bool = False) -> FileResponse:
        job = _get_job(request, job_id)
        if job.status != JobStatus.COMPLETED or job.video_path is None or not job.video_path.is_file():
            raise HTTPException(status_code=404, detail="The video is not ready yet.")
        headers = {"Cache-Control": "private, max-age=3600"}
        if download:
            title = job.storyboard.title if job.storyboard is not None else ""
            headers["Content-Disposition"] = f'attachment; filename="{slugify(title)}.mp4"'
        return FileResponse(job.video_path, media_type="video/mp4", headers=headers)

    @app.get("/api/jobs/{job_id}/code")
    def get_code(job_id: str, request: Request) -> PlainTextResponse:
        job = _get_job(request, job_id)
        candidates: list[tuple[Path, str]] = []
        if job.code_path is not None:
            candidates.append((job.code_path, "final"))
        candidates.append((job.job_dir / "scene.py", "final"))
        # Failed/in-progress jobs: show the latest attempt's code for debugging.
        for attempt in reversed(_attempt_dirs(job.job_dir)):
            candidates.append((attempt / "scene.py", f"attempt-{attempt.name}"))
        for path, source in candidates:
            if path.is_file():
                return PlainTextResponse(
                    path.read_text(encoding="utf-8", errors="replace"),
                    media_type="text/plain; charset=utf-8",
                    headers={"X-Code-Source": source},
                )
        raise HTTPException(status_code=404, detail="No code has been generated for this job yet.")

    @app.get("/api/jobs/{job_id}/debug")
    def get_debug(job_id: str, request: Request) -> dict[str, Any]:
        job = _get_job(request, job_id)
        attempts_json: Any = None
        attempts_file = job.job_dir / "attempts.json"
        if attempts_file.is_file():
            try:
                attempts_json = json.loads(attempts_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                attempts_json = {"error": f"could not read attempts.json: {exc}"}
        attempt_logs = [
            {
                "attempt": d.name,
                "has_code": (d / "scene.py").is_file(),
                "render_log_tail": _tail(d / "render.log") if (d / "render.log").is_file() else None,
            }
            for d in _attempt_dirs(job.job_dir)
        ]
        return {
            "job_id": job.id,
            "mode": job.mode,
            "prompt": job.prompt,
            "status": job.status.value,
            "stage_index": job.stage_index,
            "detail": job.detail,
            "error": job.error,
            "error_detail": job.error_detail,
            "attempts": job.attempts,
            "duration_seconds": job.duration,
            "timings": job.timings(),
            "timeline": job.timeline,
            "storyboard": job.storyboard.model_dump() if job.storyboard is not None else None,
            "attempts_json": attempts_json,
            "attempt_logs": attempt_logs,
            "job_dir": str(job.job_dir),
            "files": sorted(
                str(p.relative_to(job.job_dir))
                for p in job.job_dir.rglob("*")
                if p.is_file() and "media" not in p.relative_to(job.job_dir).parts
            )
            if job.job_dir.is_dir()
            else [],
        }

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        settings = get_settings()
        return HealthResponse(
            ok=True,
            anthropic_configured=settings.anthropic_configured,
            planner_model=settings.planner_model,
            codegen_model=settings.codegen_model,
            sandbox=_sandbox_status(),
            offline_demo_available=offline_demo.is_available(),
        )

    return app


app = create_app()
