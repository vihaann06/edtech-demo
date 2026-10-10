from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.jobs import GENERIC_ERROR, Job, new_job_id
from app.llm import LLMRefusalError
from app.pipeline import offline_demo
from app.pipeline.orchestrator import run_job
from app.pipeline.types import JobStatus, PipelineError
from tests.conftest import FakePlanner, FakeProducer, make_storyboard


@pytest.fixture
def job(isolated_settings: Path) -> Job:
    job_id = new_job_id()
    return Job(id=job_id, prompt="Explain how self-attention works.", mode="live", job_dir=isolated_settings / job_id)


def _statuses(job: Job) -> list[str]:
    return [e["status"] for e in job.timeline]


def test_live_job_happy_path(job):
    planner, producer = FakePlanner(), FakeProducer()
    run_job(job, planner=planner, producer=producer)

    assert job.status == JobStatus.COMPLETED, job.error_detail
    assert job.stage_index == 4
    assert _statuses(job) == [
        "queued", "planning", "designing", "generating_code", "rendering", "validating", "completed"
    ]
    assert planner.calls == ["Explain how self-attention works."]
    assert producer.calls[0]["code"] is None
    assert (job.job_dir / "prompt.txt").read_text().strip() == job.prompt
    saved = json.loads((job.job_dir / "storyboard.json").read_text())
    assert saved["title"] == "How Self-Attention Works"
    assert job.storyboard.title == "How Self-Attention Works"
    assert job.video_path == job.job_dir / "final.mp4"
    assert job.code_path == job.job_dir / "scene.py"
    assert job.duration == 30.0 and job.attempts == 1
    record = json.loads((job.job_dir / "job.json").read_text())
    assert record["status"] == "completed" and "rendering" in record["timings"]


def test_repairs_are_reported_and_counted(job):
    run_job(job, planner=FakePlanner(), producer=FakeProducer(failed_attempts=2))
    assert job.status == JobStatus.COMPLETED
    assert job.attempts == 3
    assert _statuses(job).count("repairing") == 2
    assert job.timings()["rendering"] >= 0


def test_planner_failure_fails_with_user_message(job):
    planner = FakePlanner(error=PipelineError("That topic is too broad. Try something more specific.", detail="raw json"))
    producer = FakeProducer()
    run_job(job, planner=planner, producer=producer)

    assert job.status == JobStatus.FAILED
    assert job.error == "That topic is too broad. Try something more specific."
    assert "raw json" in job.error_detail
    assert job.stage_index == 1  # reached "Designing" before failing
    assert producer.calls == []
    assert job.storyboard is None


def test_llm_errors_surface_their_user_message(job):
    run_job(job, planner=FakePlanner(error=LLMRefusalError("The AI declined to explain this topic.")), producer=FakeProducer())
    assert job.status == JobStatus.FAILED
    assert job.error == "The AI declined to explain this topic."


def test_producer_failure_after_retries(job):
    error = PipelineError("We couldn't render this animation after several tries.", detail="NameError: foo")
    run_job(job, planner=FakePlanner(), producer=FakeProducer(error=error, failed_attempts=3))

    assert job.status == JobStatus.FAILED
    assert job.error == "We couldn't render this animation after several tries."
    assert "NameError: foo" in job.error_detail
    assert job.stage_index == 3
    assert job.attempts == 3  # counted from attempts/<n>/ dirs
    assert job.storyboard is not None  # the UI can still show the plan
    assert job.video_path is None


def test_unexpected_exception_gets_generic_message(job):
    run_job(job, planner=FakePlanner(error=KeyError("secret internals")), producer=FakeProducer())
    assert job.status == JobStatus.FAILED
    assert job.error == GENERIC_ERROR
    assert "secret internals" not in job.error
    assert "KeyError" in job.error_detail and "Traceback" in job.error_detail


def test_missing_video_file_fails(job):
    run_job(job, planner=FakePlanner(), producer=FakeProducer(write_video=False))
    assert job.status == JobStatus.FAILED
    assert job.error == "The video file was not produced."


def test_planner_returning_wrong_type_fails_generically(job):
    run_job(job, planner=lambda prompt, on_progress: {"title": "dict"}, producer=FakeProducer())
    assert job.status == JobStatus.FAILED
    assert job.error == GENERIC_ERROR


def test_never_raises_even_if_job_dir_cannot_be_created(job):
    job.job_dir.parent.mkdir(parents=True, exist_ok=True)
    job.job_dir.write_text("a file where the directory should be")
    run_job(job, planner=FakePlanner(), producer=FakeProducer())
    assert job.status == JobStatus.FAILED
    assert job.error == GENERIC_ERROR


def test_missing_planner_module_fails_cleanly(job, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.pipeline.lesson_planner", None)  # makes the import raise ImportError
    run_job(job, producer=FakeProducer())
    assert job.status == JobStatus.FAILED
    assert job.error == "The lesson planner isn't available on this server."


def test_missing_production_module_fails_cleanly(job, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.pipeline.production", None)
    run_job(job, planner=FakePlanner())
    assert job.status == JobStatus.FAILED
    assert job.error == "The video renderer isn't available on this server."
    assert job.storyboard is not None


# --- offline demo --------------------------------------------------------------------------------


@pytest.fixture
def demo_fixture(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "offline_demo"
    d.mkdir()
    (d / "storyboard.json").write_text(make_storyboard("How Gradient Descent Works").model_dump_json())
    (d / "scene.py").write_text("# hand-written demo scene\nfrom manim import *\n")
    monkeypatch.setattr(offline_demo, "OFFLINE_DEMO_DIR", d)
    return d


def test_offline_demo_uses_fixture_not_prompt(job, demo_fixture):
    job.mode = "offline_demo"
    planner, producer = FakePlanner(), FakeProducer()
    run_job(job, planner=planner, producer=producer)

    assert job.status == JobStatus.COMPLETED, job.error_detail
    assert job.mode == "offline_demo"
    assert planner.calls == []  # the prompt never reaches an AI planner
    assert producer.calls[0]["code"] == "# hand-written demo scene\nfrom manim import *\n"
    assert producer.calls[0]["storyboard"].title == "How Gradient Descent Works"
    assert job.storyboard.title == "How Gradient Descent Works"
    assert (job.job_dir / "prompt.txt").read_text().strip() == job.prompt
    # Walks through the same stages, with honest details.
    assert _statuses(job) == ["queued", "planning", "designing", "generating_code", "rendering", "validating", "completed"]
    details = [e["detail"] or "" for e in job.timeline[1:4]]
    assert all(d.startswith("Offline demo") for d in details)


def test_offline_demo_missing_fixture_fails_clearly(job, tmp_path, monkeypatch):
    monkeypatch.setattr(offline_demo, "OFFLINE_DEMO_DIR", tmp_path / "nope")
    job.mode = "offline_demo"
    run_job(job, planner=FakePlanner(), producer=FakeProducer())
    assert job.status == JobStatus.FAILED
    assert job.error == "The offline demo isn't installed on this server."


def test_offline_demo_invalid_storyboard_fails_clearly(job, demo_fixture):
    (demo_fixture / "storyboard.json").write_text('{"title": "x"}')
    job.mode = "offline_demo"
    run_job(job, planner=FakePlanner(), producer=FakeProducer())
    assert job.status == JobStatus.FAILED
    assert job.error == "The offline demo lesson is invalid."
