"""Runtime configuration, read from environment variables (and the repo-root .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent

# Load the repo-root .env once. Real environment variables take precedence.
load_dotenv(ROOT_DIR / ".env", override=False)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # --- AI -----------------------------------------------------------------
    anthropic_workspace_id: str  # "" unless the key is user-scoped and needs a workspace header
    planner_model: str
    codegen_model: str
    planner_effort: str  # "" means: don't send an effort level
    codegen_effort: str
    repair_effort: str
    refusal_fallback: bool

    # --- Rendering ----------------------------------------------------------
    renderer_image: str
    render_width: int
    render_height: int
    render_fps: int
    render_timeout_s: int
    render_cpus: str
    render_memory: str
    max_repair_attempts: int

    # --- Lesson shape -------------------------------------------------------
    target_duration_s: float
    duration_tolerance_s: float

    # --- Jobs / server ------------------------------------------------------
    jobs_dir: Path
    job_ttl_hours: float
    max_concurrent_jobs: int
    allow_offline_demo: bool
    cors_origins: list[str] = field(default_factory=list)

    # Paths that are fixed relative to the repo.
    manim_helpers_dir: Path = BACKEND_DIR / "manim"

    @property
    def anthropic_configured(self) -> bool:
        """True if the Anthropic SDK can find credentials in the environment."""
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    default_model = _env("ANTHROPIC_MODEL", "claude-opus-5-5")
    return Settings(
        anthropic_workspace_id=_env("ANTHROPIC_WORKSPACE_ID", ""),
        planner_model=_env("PLANNER_MODEL", default_model),
        codegen_model=_env("CODEGEN_MODEL", default_model),
        planner_effort=_env("PLANNER_EFFORT", "medium"),
        codegen_effort=_env("CODEGEN_EFFORT", "medium"),
        repair_effort=_env("REPAIR_EFFORT", "medium"),
        refusal_fallback=_env_bool("ANTHROPIC_REFUSAL_FALLBACK", True),
        renderer_image=_env("RENDERER_IMAGE", "explainer-manim-renderer:0.19.2"),
        render_width=_env_int("RENDER_WIDTH", 1920),
        render_height=_env_int("RENDER_HEIGHT", 1080),
        render_fps=_env_int("RENDER_FPS", 30),
        render_timeout_s=_env_int("RENDER_TIMEOUT_S", 420),
        render_cpus=_env("RENDER_CPUS", "3"),
        render_memory=_env("RENDER_MEMORY", "3g"),
        max_repair_attempts=_env_int("MAX_REPAIR_ATTEMPTS", 2),
        target_duration_s=_env_float("TARGET_DURATION_S", 30.0),
        duration_tolerance_s=_env_float("DURATION_TOLERANCE_S", 3.0),
        # Relative JOBS_DIR values are relative to the repo root, wherever the server is started from.
        jobs_dir=(ROOT_DIR / _env("JOBS_DIR", str(BACKEND_DIR / ".jobs"))).resolve(),
        job_ttl_hours=_env_float("JOB_TTL_HOURS", 6.0),
        max_concurrent_jobs=_env_int("MAX_CONCURRENT_JOBS", 2),
        allow_offline_demo=_env_bool("ALLOW_OFFLINE_DEMO", True),
        cors_origins=[
            o.strip()
            for o in _env("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
            if o.strip()
        ],
    )
