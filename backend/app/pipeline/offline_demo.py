"""The explicitly-labelled offline demo: a fixed, hand-written lesson.

Used only when a request asks for `mode="offline_demo"` (e.g. no API key at a
venue). The user's prompt is ignored, never topic-matched, and the job keeps
`mode="offline_demo"` so the UI can say so. Fixture files are owned by Agent 3.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from app.config import BACKEND_DIR, get_settings
from app.pipeline.schema import Storyboard
from app.pipeline.types import PipelineError

OFFLINE_DEMO_DIR = BACKEND_DIR / "manim" / "offline_demo"
STORYBOARD_FILE = "storyboard.json"
SCENE_FILE = "scene.py"


def demo_dir() -> Path:
    return OFFLINE_DEMO_DIR


def fixture_present() -> bool:
    d = demo_dir()
    return (d / STORYBOARD_FILE).is_file() and (d / SCENE_FILE).is_file()


def is_available() -> bool:
    """Offline demo enabled in settings and its fixture files exist."""
    return get_settings().allow_offline_demo and fixture_present()


def load_offline_demo() -> tuple[Storyboard, str]:
    """Return the fixture storyboard (validated) and its hand-written Manim code."""
    d = demo_dir()
    sb_path, code_path = d / STORYBOARD_FILE, d / SCENE_FILE
    missing = [p.name for p in (sb_path, code_path) if not p.is_file()]
    if missing:
        raise PipelineError(
            "The offline demo isn't installed on this server.",
            detail=f"missing {', '.join(missing)} in {d}",
        )
    try:
        storyboard = Storyboard.model_validate_json(sb_path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise PipelineError("The offline demo lesson is invalid.", detail=f"{sb_path}: {exc}") from exc
    code = code_path.read_text(encoding="utf-8")
    if not code.strip():
        raise PipelineError("The offline demo animation code is empty.", detail=str(code_path))
    return storyboard, code
