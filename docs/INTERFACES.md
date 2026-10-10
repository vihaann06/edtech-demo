# Shared interfaces & file ownership

This is the contract between the four workstreams. If you need to change something
in here, make the change additive (new optional fields / new functions) and note it
in your final report so the lead can integrate it.

## Pipeline

```
POST /api/generate {prompt}
  -> jobs.JobManager.submit()                       [backend/app/jobs.py]        Agent 4
  -> orchestrator.run_job(job)                      [backend/app/pipeline/orchestrator.py] Agent 4
       1. lesson_planner.plan_lesson(prompt, on_progress) -> Storyboard          Agent 2
       2. production.produce_video(storyboard, job_dir, on_progress) -> ProductionResult  Agent 3
            a. manim_generator.generate_manim_code(storyboard, on_progress) -> str
            b. manim_generator.static_check(code) -> list[str]
            c. renderer.render(code, attempt_dir, ...) -> RenderResult   (Docker sandbox)
            d. on failure: manim_generator.repair_manim_code(...) -> str, retry (max 2 repairs)
            e. validator.validate_video(path, ...) -> VideoInfo          (ffprobe)
  -> job.status = completed, video at <job_dir>/final.mp4
GET /api/jobs/{id}         -> JobResponse (polled by the frontend every ~1s)
GET /api/jobs/{id}/video   -> MP4 (supports HTTP Range; ?download=1 adds Content-Disposition)
```

## Already written by the lead (read, import, don't restructure)

| File | What |
|---|---|
| `backend/app/config.py` | `get_settings()` -> `Settings` (models, effort levels, render size/fps/timeout, jobs dir, ...). All tunables come from env vars. |
| `backend/app/llm.py` | `generate_text(...)`, `generate_structured(...)`, `LLMError`, `LLMNotConfiguredError`, `LLMRefusalError`, `LLMOutputError`, `is_configured()`. **All Anthropic calls go through this module.** |
| `backend/app/pipeline/types.py` | `JobStatus` enum, `STAGE_LABELS`, `STATUS_TO_STAGE_INDEX`, `ProgressCallback = Callable[[JobStatus, str \| None], None]`, `PipelineError(user_message, detail=)`. |
| `backend/app/pipeline/schema.py` | `Storyboard`, `StoryboardScene` (seeded by the lead; **owned by Agent 2** from now on — additive changes only, field names are relied on by Agent 3). |
| `backend/requirements.txt`, `backend/pytest.ini`, `.gitignore` | Python deps (venv at `backend/.venv`, Python 3.12). Ask the lead before adding heavy deps. |

## File ownership

| Agent | Owns |
|---|---|
| 1 — Frontend & Design | everything under `frontend/` |
| 2 — Lesson Planning | `backend/app/pipeline/lesson_planner.py`, `backend/app/pipeline/schema.py`, `backend/app/pipeline/prompts/planner*.md` (if used), `backend/tests/test_lesson_planner.py`, `backend/tests/test_schema.py` |
| 3 — Manim Generation & Rendering | `backend/app/pipeline/manim_generator.py`, `backend/app/pipeline/renderer.py`, `backend/app/pipeline/validator.py`, `backend/app/pipeline/production.py`, `backend/app/pipeline/prompts/codegen*.md` / `repair*.md` (if used), `backend/manim/**` (design system + offline-demo fixture), `docker/Dockerfile.renderer`, `backend/tests/test_manim_generator.py`, `test_renderer.py`, `test_validator.py`, `test_production.py`, `test_visual_helpers.py` |
| 4 — Backend & Integration | `backend/app/main.py`, `backend/app/models.py`, `backend/app/jobs.py`, `backend/app/pipeline/orchestrator.py`, `backend/app/pipeline/offline_demo.py`, `backend/tests/test_api.py`, `backend/tests/test_jobs.py`, `backend/tests/test_orchestrator.py`, `backend/tests/conftest.py`, `Makefile`, `scripts/**` |
| Lead | `config.py`, `llm.py`, `pipeline/types.py`, `README.md`, `.env.example`, `docs/**` |

Never edit a file you don't own. If you need something from another owner, write it in your final report.

## Python interfaces

### Agent 2 — `backend/app/pipeline/lesson_planner.py`

```python
class LessonPlanningError(PipelineError): ...

def plan_lesson(prompt: str, *, on_progress: ProgressCallback = noop_progress) -> Storyboard:
    """Turn a natural-language question into a validated ~30 s storyboard.
    Calls on_progress(JobStatus.PLANNING, None) at start and
    on_progress(JobStatus.DESIGNING, <detail>) once the model starts writing the
    storyboard (llm.generate_structured's on_output_start) and while validating/retrying.
    Raises LessonPlanningError (or an LLMError) with a UI-safe user_message."""
```

### Agent 3 — `backend/app/pipeline/production.py` (+ generator / renderer / validator)

```python
SCENE_CLASS_NAME = "ExplainerVideo"          # in manim_generator.py

@dataclass
class ProductionResult:
    video_path: Path        # <job_dir>/final.mp4
    code_path: Path         # <job_dir>/scene.py  (final, successfully rendered code)
    video_info: VideoInfo   # from validator.py: duration_s, width, height, fps, codec, size_bytes
    attempts: int           # renders attempted (1 = first try worked)

class ProductionError(PipelineError): ...    # rendering failed after all repairs, validation failed, sandbox unavailable

def produce_video(storyboard: Storyboard, job_dir: Path, *, on_progress: ProgressCallback = noop_progress,
                  code: str | None = None) -> ProductionResult:
    """Generate Manim code from the storyboard (unless `code` is supplied — used by the
    offline demo), render it in the Docker sandbox, repair + retry on failure (up to
    settings.max_repair_attempts repairs), validate with ffprobe, and copy the result to
    <job_dir>/final.mp4. Reports JobStatus.GENERATING_CODE / RENDERING / REPAIRING / VALIDATING."""

def sandbox_status() -> dict:   # in renderer.py
    """{"docker_available": bool, "image_present": bool, "image": str, "error": str | None} — cheap, used by /api/health."""
```

`backend/manim/offline_demo/` (Agent 3) holds a hand-written storyboard JSON + scene.py used only by
the explicitly-labelled offline demo mode. It must NOT be topic-matched to user prompts.

### Agent 4 — orchestration

```python
def run_job(job: Job) -> None   # in orchestrator.py: plan -> produce, updates job via callbacks, never raises
```

## Job directory layout (`settings.jobs_dir / <job_id>/`)

```
prompt.txt            written by orchestrator (Agent 4)
storyboard.json       written by orchestrator after planning (Agent 4)
attempts/<n>/scene.py      code rendered in attempt n             (Agent 3)
attempts/<n>/render.log    stdout+stderr of the sandbox             (Agent 3)
attempts/<n>/media/...     manim output (mounted into the container) (Agent 3)
scene.py              final code that rendered successfully (Agent 3)
final.mp4             validated video (Agent 3)
```

## HTTP API (Agent 4 implements, Agent 1 consumes)

Base path `/api`. The Next.js app proxies `/api/*` to the backend (`BACKEND_URL`, default `http://127.0.0.1:8000`) via `rewrites`, so the browser only ever talks to the Next.js origin.

`POST /api/generate` — body `{"prompt": string, "mode"?: "live" | "offline_demo"}` (default `live`).
- 200 `{"job_id": string}`
- 422 when prompt is empty/whitespace or > 500 chars: `{"detail": "..."}` (string or FastAPI validation list)
- 503 when `mode=live` and `ANTHROPIC_API_KEY` is missing: `{"detail": "AI generation is not configured ..."}`
- 400 when `mode=offline_demo` but `ALLOW_OFFLINE_DEMO=false`

`GET /api/jobs/{job_id}` — 404 if unknown. 200:
```json
{
  "job_id": "a1b2c3",
  "prompt": "Explain how self-attention works in Transformers.",
  "mode": "live",                       // or "offline_demo" — UI must label demo results clearly
  "status": "rendering",                // JobStatus value
  "stage_index": 3,                     // 0..4, index into stages[]
  "progress_stage": "Rendering your video",
  "stages": ["Understanding your concept", "Designing your explanation", "Creating animations", "Rendering your video", "Your explanation is ready"],
  "detail": "Rendering animation 12",   // short sub-status or null
  "error": null,                        // UI-safe message when status == "failed"
  "title": "How Self-Attention Works",  // null until the storyboard exists
  "storyboard": {                        // null until planned; summary for the UI
    "title": "...", "learning_objective": "...",
    "scenes": [{"index": 0, "concept": "...", "caption": "...", "start_time": 0, "duration": 7.5}]
  },
  "video_url": "/api/jobs/a1b2c3/video", // null until completed
  "download_url": "/api/jobs/a1b2c3/video?download=1",
  "duration_seconds": 30.0,             // null until completed
  "attempts": 1,
  "created_at": "2026-10-10T18:00:00Z",
  "updated_at": "2026-10-10T18:01:10Z",
  "elapsed_seconds": 70.2
}
```

`GET /api/jobs/{job_id}/video` — `video/mp4` with Range support (FileResponse). 404 until completed.
`GET /api/jobs/{job_id}/code` — generated `scene.py` as `text/x-python` (debugging).
`GET /api/jobs/{job_id}/debug` — storyboard JSON, per-attempt logs/errors, timings (debugging).
`GET /api/health` — `{"ok": true, "anthropic_configured": bool, "planner_model": str, "codegen_model": str, "sandbox": {...sandbox_status()}, "offline_demo_available": bool}`.

## Video spec

1920x1080 (fallback 1280x720 via RENDER_WIDTH/HEIGHT), 30 fps, H.264 MP4, no audio, 27–33 s, dark background,
captions burned into the video in a reserved bottom band, 3–4 scenes timed to the storyboard.

## Testing rules

- Unit tests must not call the real Anthropic API — monkeypatch `app.llm.generate_text` / `generate_structured` (or the module-level references you import).
- Tests that need Docker are marked `@pytest.mark.docker` and skip cleanly when Docker/the image is unavailable.
- Run tests with `cd backend && .venv/bin/pytest -q`.
- No API key is configured on this machine right now. Do not claim any live AI output works.
