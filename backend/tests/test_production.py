"""Tests for production.produce_video: the generate -> check -> render -> repair -> validate loop."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

import pytest

from app import llm
from app.config import BACKEND_DIR
from app.pipeline import manim_generator, production, renderer, validator
from app.pipeline.production import ProductionError
from app.pipeline.renderer import RenderResult, SandboxUnavailableError
from app.pipeline.schema import Storyboard
from app.pipeline.types import JobStatus
from app.pipeline.validator import VideoInfo, VideoValidationError

FIXTURE_DIR = BACKEND_DIR / "manim" / "offline_demo"


@pytest.fixture
def storyboard() -> Storyboard:
    return Storyboard.model_validate_json((FIXTURE_DIR / "storyboard.json").read_text())


@pytest.fixture
def good_code() -> str:
    return (FIXTURE_DIR / "scene.py").read_text()


def good_timeline(sb: Storyboard) -> list[dict[str, Any]]:
    out, t = [], 0.0
    for s in sb.scenes:
        out.append({"index": s.index, "planned": s.duration, "actual": s.duration, "start": t, "end": t + s.duration})
        t += s.duration
    return out


class FakeRenderer:
    """Stands in for renderer.render. `outcomes` is a list of "ok" | "error" | "timeout" | "late"."""

    def __init__(self, storyboard: Storyboard, outcomes: list[str]) -> None:
        self.sb = storyboard
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, code: str, attempt_dir: Path, *, timeout_s=None, on_log=None, storyboard_json=None,
                 on_queue=None, **kw) -> RenderResult:
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        self.calls.append({"code": code, "dir": attempt_dir, "storyboard_json": storyboard_json, "kw": kw})
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "scene.py").write_text(code)
        if on_log:
            on_log('EXPLAINER_BLOCK_START {"index":0,"total":4}')
            on_log("INFO Animation 0 : Partial movie file written in '/work/x.mp4'")
            on_log("INFO Animation 1 : Partial movie file written in '/work/y.mp4'")
            on_log("INFO Combining to Movie file.")
        if outcome in ("ok", "late"):
            video = attempt_dir / "media" / "ExplainerVideo.mp4"
            video.parent.mkdir(parents=True, exist_ok=True)
            video.write_bytes(b"fake-mp4" * 100)
            tl = good_timeline(self.sb)
            if outcome == "late":
                tl[2]["end"] += 1.0
            return RenderResult(success=True, video_path=video, stdout="ok", log_text="ok", exit_code=0,
                                timed_out=False, wall_time_s=1.0, timeline=tl,
                                summary={"duration": self.sb.total_duration, "frames": 900})
        tb = ('Traceback (most recent call last):\n  File "/work/scene.py", line 3, in construct\n'
              "NameError: name 'oops' is not defined")
        return RenderResult(success=False, video_path=None, stdout=tb, log_text=tb, exit_code=1,
                            timed_out=outcome == "timeout", wall_time_s=1.0, traceback=tb if outcome == "error" else "")


def fake_info() -> VideoInfo:
    return VideoInfo(duration_s=30.0, width=1920, height=1080, fps=30.0, codec="h264", size_bytes=800, pix_fmt="yuv420p",
                     nb_frames=900)


@pytest.fixture
def env(monkeypatch, storyboard, good_code):
    """Patch the sandbox, validator and LLM. Returns a namespace to configure each test."""

    class Env:
        renderer = FakeRenderer(storyboard, [])
        repairs: list[dict[str, Any]] = []
        generations: list[dict[str, Any]] = []
        validate_error: Optional[VideoValidationError] = None
        repair_answer = f"```python\n{good_code}```"
        events: list[tuple[JobStatus, Optional[str]]] = []

        def progress(self, status, detail=None):
            self.events.append((status, detail))

    e = Env()
    monkeypatch.setattr(renderer, "ensure_sandbox", lambda: None)
    monkeypatch.setattr(renderer, "render", lambda *a, **k: e.renderer(*a, **k))

    def fake_validate(path, **kw):
        if e.validate_error is not None:
            raise e.validate_error
        return fake_info()

    monkeypatch.setattr(validator, "validate_video", fake_validate)

    def fake_generate_text(**kw):
        if kw["purpose"] == "animation code repair":
            e.repairs.append(kw)
            return llm.LLMResult(text=e.repair_answer, model="fake", stop_reason="end_turn", duration_s=0.1)
        e.generations.append(kw)
        return llm.LLMResult(text=f"```python\n{good_code}```", model="fake", stop_reason="end_turn", duration_s=0.1)

    monkeypatch.setattr(llm, "generate_text", fake_generate_text)
    return e


def test_first_try_success_with_supplied_code(env, storyboard, good_code, tmp_path):
    env.renderer = FakeRenderer(storyboard, ["ok"])
    r = production.produce_video(storyboard, tmp_path, on_progress=env.progress, code=good_code)
    assert r.attempts == 1 and not env.repairs and not env.generations
    assert r.video_path == tmp_path / "final.mp4" and r.video_path.read_bytes().startswith(b"fake-mp4")
    assert r.code_path.read_text() == good_code
    assert (tmp_path / "attempts" / "1" / "scene.py").exists()
    records = json.loads((tmp_path / "attempts.json").read_text())
    assert records[0]["stage"] == "ok" and records[0]["ok"] is True
    # The storyboard manifest is handed to the sandbox.
    assert json.loads(env.renderer.calls[0]["storyboard_json"])["title"] == storyboard.title
    statuses = [s for s, _ in env.events]
    assert JobStatus.RENDERING in statuses and statuses[-1] == JobStatus.VALIDATING
    details = [d for s, d in env.events if s == JobStatus.RENDERING]
    assert "Rendering animation 2 (scene 1 of 4)" in details and "Assembling the video" in details


def test_generates_code_when_none_supplied(env, storyboard, tmp_path):
    env.renderer = FakeRenderer(storyboard, ["ok"])
    r = production.produce_video(storyboard, tmp_path, on_progress=env.progress)
    assert r.attempts == 1 and len(env.generations) == 1
    assert env.events[0] == (JobStatus.GENERATING_CODE, "Planning the animations")


def test_render_failure_is_repaired(env, storyboard, good_code, tmp_path):
    env.renderer = FakeRenderer(storyboard, ["error", "ok"])
    r = production.produce_video(storyboard, tmp_path, on_progress=env.progress, code=good_code)
    assert r.attempts == 2 and len(env.repairs) == 1
    msg = env.repairs[0]["messages"]
    assert "NameError: name 'oops' is not defined" in msg and "Your code around line 3" in msg
    assert (JobStatus.REPAIRING, "Fixing an animation issue (attempt 2 of 3)") in env.events
    records = json.loads((tmp_path / "attempts.json").read_text())
    assert [x["stage"] for x in records] == ["render", "ok"]
    assert records[1]["code_source"] == "repaired"


def test_static_check_failure_goes_to_repair_without_rendering(env, storyboard, good_code, tmp_path):
    env.renderer = FakeRenderer(storyboard, ["ok"])
    bad = good_code.replace("import *\nfrom visual_helpers", "import *\nimport os\nfrom visual_helpers", 1)
    r = production.produce_video(storyboard, tmp_path, on_progress=env.progress, code=bad)
    assert r.attempts == 2 and len(env.renderer.calls) == 1
    assert "rejected before rendering" in env.repairs[0]["messages"] and "Import of 'os'" in env.repairs[0]["messages"]


def test_gives_up_after_all_repairs(env, storyboard, good_code, tmp_path):
    env.renderer = FakeRenderer(storyboard, ["error", "timeout", "error"])
    with pytest.raises(ProductionError) as exc:
        production.produce_video(storyboard, tmp_path, on_progress=env.progress, code=good_code)
    assert exc.value.user_message == "We couldn't render this animation after 3 attempts."
    assert "NameError" in exc.value.detail
    assert len(env.repairs) == 2 and len(env.renderer.calls) == 3
    assert not (tmp_path / "final.mp4").exists()
    stages = [x["stage"] for x in json.loads((tmp_path / "attempts.json").read_text())]
    assert stages == ["render", "timeout", "render"]
    assert "time limit" in env.repairs[1]["messages"]


def test_repair_budget_comes_from_settings(env, storyboard, good_code, tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_REPAIR_ATTEMPTS", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    env.renderer = FakeRenderer(storyboard, ["error"])
    with pytest.raises(ProductionError) as exc:
        production.produce_video(storyboard, tmp_path, code=good_code)
    assert "after 1 attempt." in exc.value.user_message and not env.repairs


def test_unrepairable_validation_failure_fails_fast(env, storyboard, good_code, tmp_path):
    env.renderer = FakeRenderer(storyboard, ["ok", "ok"])
    env.validate_error = VideoValidationError("bad pixels", detail="pix_fmt=yuv444p", kind="pix_fmt")
    with pytest.raises(ProductionError):
        production.produce_video(storyboard, tmp_path, code=good_code)
    assert not env.repairs and len(env.renderer.calls) == 1


def test_duration_validation_failure_is_repaired(env, storyboard, good_code, tmp_path):
    env.renderer = FakeRenderer(storyboard, ["ok", "ok"])
    env.validate_error = VideoValidationError("wrong length", detail="duration 12s", kind="duration")

    def clear_after_repair(**kw):
        env.validate_error = None
        return llm.LLMResult(text=env.repair_answer, model="fake", stop_reason="end_turn", duration_s=0.1)

    import app.llm as llm_mod

    original = llm_mod.generate_text
    llm_mod.generate_text = clear_after_repair
    try:
        r = production.produce_video(storyboard, tmp_path, code=good_code)
    finally:
        llm_mod.generate_text = original
    assert r.attempts == 2


def test_timing_problem_is_repaired_then_accepted_on_last_attempt(env, storyboard, good_code, tmp_path):
    env.renderer = FakeRenderer(storyboard, ["late", "late", "late"])
    r = production.produce_video(storyboard, tmp_path, code=good_code)
    assert r.attempts == 3 and len(env.repairs) == 2
    assert "scene 2 ended at" in env.repairs[0]["messages"]
    records = json.loads((tmp_path / "attempts.json").read_text())
    assert records[-1]["ok"] and any("timing deviations" in n for n in records[-1]["notes"])


def test_sandbox_unavailable_is_a_production_error_before_any_llm_call(env, storyboard, tmp_path, monkeypatch):
    def unavailable():
        raise SandboxUnavailableError("The video renderer isn't available right now.", detail="docker missing")

    monkeypatch.setattr(renderer, "ensure_sandbox", unavailable)
    with pytest.raises(ProductionError) as exc:
        production.produce_video(storyboard, tmp_path)
    assert "renderer" in exc.value.user_message and exc.value.detail == "docker missing"
    assert not env.generations


def test_offline_failure_without_api_key_is_a_clean_production_error(storyboard, good_code, tmp_path, monkeypatch):
    monkeypatch.setattr(renderer, "ensure_sandbox", lambda: None)
    monkeypatch.setattr(renderer, "render", FakeRenderer(storyboard, ["error"]))
    with pytest.raises(ProductionError) as exc:
        production.produce_video(storyboard, tmp_path, code=good_code)  # no ANTHROPIC_API_KEY in tests
    assert "repair isn't available" in exc.value.user_message


def test_repair_without_code_is_retried_once_then_fails(env, storyboard, good_code, tmp_path):
    env.renderer = FakeRenderer(storyboard, ["error", "ok"])
    env.repair_answer = "I am not sure how to fix this."
    with pytest.raises(ProductionError) as exc:
        production.produce_video(storyboard, tmp_path, code=good_code)
    assert len(env.repairs) == 2 and len(env.renderer.calls) == 1
    assert "no code twice" in exc.value.detail


# --------------------------------------------------------------------------------------
# Docker: the real offline fixture through the real pipeline (no LLM)
# --------------------------------------------------------------------------------------


@pytest.mark.docker
def test_offline_fixture_renders_for_real(storyboard, good_code):
    renderer._status_cache = None
    st = renderer.sandbox_status(refresh=True)
    if not (st["docker_available"] and st["image_present"]):
        pytest.skip("Docker or the renderer image is not available")
    job_dir = BACKEND_DIR / ".jobs" / "pytest-agent3-production"
    shutil.rmtree(job_dir, ignore_errors=True)
    events = []
    try:
        r = production.produce_video(storyboard, job_dir, code=good_code, on_progress=lambda s, d=None: events.append((s, d)),
                                     render_kwargs={"width": 854, "height": 480})
        assert r.attempts == 1
        assert abs(r.video_info.duration_s - storyboard.total_duration) <= 1 / 30 + 1e-6
        assert (r.video_info.width, r.video_info.height, r.video_info.fps) == (854, 480, 30.0)
        assert r.video_path.exists() and r.code_path.read_text() == good_code
        ends = [e["end"] for e in r.timeline]
        assert ends == [7.0, 15.0, 23.0, 30.0]
        assert all(e["clamped_calls"] == 0 and e["layout_fixes"] == 0 for e in r.timeline)
        assert any(d and d.startswith("Rendering animation") for _, d in events)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
