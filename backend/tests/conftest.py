"""Shared fixtures for the backend/integration tests (Agent 4).

Nothing here calls the Anthropic API or Docker: the planner and producer are
replaced by fakes that report the same progress statuses the real ones do.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import pytest

from app.config import get_settings
from app.pipeline.schema import Storyboard
from app.pipeline.types import JobStatus

FAKE_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(range(256)) * 64  # ~16 KB


@pytest.fixture(autouse=True)
def isolated_settings(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Every test gets its own JOBS_DIR and no Anthropic credentials (unless it sets them).

    Tests marked `live` keep the real credentials (they are deselected by default).
    """
    jobs_dir = tmp_path / "jobs"
    monkeypatch.setenv("JOBS_DIR", str(jobs_dir))
    if request.node.get_closest_marker("live") is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ALLOW_OFFLINE_DEMO", "true")
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "2")
    monkeypatch.setenv("JOB_TTL_HOURS", "6")
    get_settings.cache_clear()
    yield jobs_dir
    get_settings.cache_clear()


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend credentials exist (the fakes never call the API)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    get_settings.cache_clear()


def make_storyboard(title: str = "How Self-Attention Works") -> Storyboard:
    scenes = []
    for i, concept in enumerate(["Tokens", "Attention weights", "Weighted sum"]):
        scenes.append(
            {
                "index": i,
                "concept": concept,
                "start_time": i * 10.0,
                "duration": 10.0,
                "caption": f"Scene {i + 1}: each word looks at every other word.",
                "visual_description": "Boxes and arrows.",
                "animation_instructions": ["Fade in the boxes", "Draw arrows"],
                "math_expressions": [],
                "visual_objects": ["token boxes"],
                "transition": "fade",
            }
        )
    return Storyboard.model_validate(
        {
            "topic": "self-attention",
            "title": title,
            "target_duration": 30.0,
            "learning_objective": "Understand how each token weighs the others.",
            "scenes": scenes,
        }
    )


@dataclass
class FakeVideoInfo:
    duration_s: float = 30.0
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    codec: str = "h264"
    size_bytes: int = len(FAKE_MP4_BYTES)


@dataclass
class FakeProductionResult:
    video_path: Path
    code_path: Path
    video_info: FakeVideoInfo
    attempts: int


class FakePlanner:
    def __init__(self, storyboard: Optional[Storyboard] = None, error: Optional[BaseException] = None) -> None:
        self.storyboard = storyboard or make_storyboard()
        self.error = error
        self.calls: list[str] = []

    def __call__(self, prompt: str, *, on_progress: Callable[..., None]) -> Storyboard:
        self.calls.append(prompt)
        on_progress(JobStatus.PLANNING, None)
        on_progress(JobStatus.DESIGNING, "Writing the storyboard")
        if self.error is not None:
            raise self.error
        on_progress(JobStatus.DESIGNING, "Checking scene timing")
        return self.storyboard


class FakeProducer:
    """Mimics production.produce_video: reports statuses, writes attempt dirs + final files."""

    def __init__(
        self,
        *,
        error: Optional[BaseException] = None,
        failed_attempts: int = 0,
        gate: Optional[threading.Event] = None,
        write_video: bool = True,
    ) -> None:
        self.error = error
        self.failed_attempts = failed_attempts
        self.gate = gate  # when set, blocks in RENDERING until the event is set
        self.write_video = write_video
        self.calls: list[dict[str, Any]] = []

    def __call__(self, storyboard: Storyboard, job_dir: Path, *, on_progress: Callable[..., None], code: Optional[str] = None):
        self.calls.append({"storyboard": storyboard, "job_dir": job_dir, "code": code})
        if code is None:
            on_progress(JobStatus.GENERATING_CODE, "Writing animation code")
            code = "from manim import *\n\nclass ExplainerVideo(Scene):\n    def construct(self):\n        pass\n"
        # `failed_attempts` renders fail first. With `error` set, the last failed render raises it
        # (i.e. "gave up after repairs"); with failed_attempts=0 it is raised after a good render
        # (i.e. "validation failed").
        attempt = 0
        while True:
            attempt += 1
            adir = job_dir / "attempts" / str(attempt)
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "scene.py").write_text(code)
            on_progress(JobStatus.RENDERING, f"Rendering attempt {attempt}")
            failed = attempt <= self.failed_attempts
            (adir / "render.log").write_text(f"render attempt {attempt}\n" + ("Traceback: boom\n" if failed else "ok\n"))
            if self.gate is not None:
                assert self.gate.wait(10), "test gate never released"
            if not failed:
                break
            if self.error is not None and attempt == self.failed_attempts:
                raise self.error
            on_progress(JobStatus.REPAIRING, f"Fixing a rendering error (attempt {attempt})")
        if self.error is not None:
            raise self.error
        on_progress(JobStatus.VALIDATING, "Checking the video")
        video = job_dir / "final.mp4"
        if self.write_video:
            video.write_bytes(FAKE_MP4_BYTES)
        scene = job_dir / "scene.py"
        scene.write_text(code)
        return FakeProductionResult(video_path=video, code_path=scene, video_info=FakeVideoInfo(), attempts=attempt)


def wait_for(predicate: Callable[[], Any], timeout: float = 10.0, interval: float = 0.02) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")


@pytest.fixture
def make_client():
    """Factory: TestClient for an app whose jobs run with the given fake planner/producer."""
    from fastapi.testclient import TestClient

    from app.jobs import JobManager
    from app.main import create_app
    from app.pipeline.orchestrator import run_job

    clients: list[TestClient] = []

    def factory(planner: Optional[Callable] = None, producer: Optional[Callable] = None, **manager_kwargs) -> TestClient:
        manager = JobManager(
            runner=partial(run_job, planner=planner or FakePlanner(), producer=producer or FakeProducer()),
            **manager_kwargs,
        )
        client = TestClient(create_app(job_manager=manager))
        client.__enter__()  # run lifespan (startup cleanup etc.)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


def poll_job(client, job_id: str, statuses=("completed", "failed"), timeout: float = 10.0) -> dict:
    def check():
        body = client.get(f"/api/jobs/{job_id}").json()
        return body if body["status"] in statuses else None

    return wait_for(check, timeout=timeout)
