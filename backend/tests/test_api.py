from __future__ import annotations

import sys
import threading

import pytest

from app.pipeline import offline_demo
from app.pipeline.types import STAGE_LABELS, PipelineError
from tests.conftest import FAKE_MP4_BYTES, FakePlanner, FakeProducer, make_storyboard, poll_job, wait_for


# --- POST /api/generate validation ---------------------------------------------------------------


@pytest.mark.parametrize("prompt", ["", "   ", "\n\t "])
def test_empty_prompt_is_422(make_client, api_key, prompt):
    res = make_client().post("/api/generate", json={"prompt": prompt})
    assert res.status_code == 422
    assert res.json()["detail"] == "Please enter a question or concept to explain."


def test_too_long_prompt_is_422(make_client, api_key):
    res = make_client().post("/api/generate", json={"prompt": "a" * 501})
    assert res.status_code == 422
    assert "500" in res.json()["detail"]


def test_prompt_is_stripped_and_500_chars_ok(make_client, api_key):
    client = make_client()
    res = client.post("/api/generate", json={"prompt": "  " + "a" * 500 + "  "})
    assert res.status_code == 200
    body = client.get(f"/api/jobs/{res.json()['job_id']}").json()
    assert body["prompt"] == "a" * 500


@pytest.mark.parametrize(
    "payload", [{}, {"prompt": 5}, {"prompt": "hi", "mode": "turbo"}, None]
)
def test_malformed_requests_are_422_with_string_detail(make_client, api_key, payload):
    res = make_client().post("/api/generate", json=payload)
    assert res.status_code == 422
    assert isinstance(res.json()["detail"], str)


def test_live_without_api_key_is_503(make_client):
    res = make_client().post("/api/generate", json={"prompt": "Explain entropy"})
    assert res.status_code == 503
    assert res.json()["detail"].startswith("AI generation is not configured")
    assert "ANTHROPIC_API_KEY" in res.json()["detail"]


def test_offline_demo_disabled_is_400(make_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("ALLOW_OFFLINE_DEMO", "false")
    get_settings.cache_clear()
    res = make_client().post("/api/generate", json={"prompt": "x", "mode": "offline_demo"})
    assert res.status_code == 400
    assert "disabled" in res.json()["detail"]


# --- job lifecycle -------------------------------------------------------------------------------


def test_full_lifecycle_and_video_serving(make_client, api_key):
    client = make_client()
    res = client.post("/api/generate", json={"prompt": "Explain how self-attention works in Transformers."})
    assert res.status_code == 200
    job_id = res.json()["job_id"]

    first = client.get(f"/api/jobs/{job_id}").json()
    assert first["status"] in {"queued", "planning", "designing", "generating_code", "rendering", "validating", "completed"}

    body = poll_job(client, job_id)
    assert body["status"] == "completed", body
    assert body["job_id"] == job_id
    assert body["mode"] == "live"
    assert body["stage_index"] == 4
    assert body["stages"] == STAGE_LABELS
    assert body["progress_stage"] == "Your explanation is ready"
    assert body["error"] is None
    assert body["title"] == "How Self-Attention Works"
    assert body["storyboard"]["learning_objective"]
    assert [s["index"] for s in body["storyboard"]["scenes"]] == [0, 1, 2]
    assert set(body["storyboard"]["scenes"][0]) == {"index", "concept", "caption", "start_time", "duration"}
    assert body["video_url"] == f"/api/jobs/{job_id}/video"
    assert body["download_url"] == f"/api/jobs/{job_id}/video?download=1"
    assert body["duration_seconds"] == 30.0
    assert body["attempts"] == 1
    assert body["created_at"].endswith("Z") and body["updated_at"].endswith("Z")
    assert body["elapsed_seconds"] >= 0

    video = client.get(body["video_url"])
    assert video.status_code == 200
    assert video.headers["content-type"] == "video/mp4"
    assert video.headers["accept-ranges"] == "bytes"
    assert video.content == FAKE_MP4_BYTES
    assert "content-disposition" not in video.headers

    ranged = client.get(body["video_url"], headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == f"bytes 0-99/{len(FAKE_MP4_BYTES)}"
    assert ranged.content == FAKE_MP4_BYTES[:100]

    tail = client.get(body["video_url"], headers={"Range": "bytes=-10"})
    assert tail.status_code == 206 and tail.content == FAKE_MP4_BYTES[-10:]

    head = client.head(body["video_url"])
    assert head.status_code == 200 and int(head.headers["content-length"]) == len(FAKE_MP4_BYTES)

    download = client.get(body["download_url"])
    assert download.status_code == 200
    assert download.headers["content-disposition"] == 'attachment; filename="how-self-attention-works.mp4"'

    code = client.get(f"/api/jobs/{job_id}/code")
    assert code.status_code == 200
    assert code.headers["content-type"].startswith("text/plain")
    assert "class ExplainerVideo" in code.text

    debug = client.get(f"/api/jobs/{job_id}/debug").json()
    assert debug["storyboard"]["scenes"][0]["visual_description"]
    assert debug["timings"]["total"] >= 0
    assert [a["attempt"] for a in debug["attempt_logs"]] == ["1"]
    assert "render attempt 1" in debug["attempt_logs"][0]["render_log_tail"]
    assert "final.mp4" in debug["files"]


def test_in_progress_job_reports_stage_and_video_is_404(make_client, api_key):
    gate = threading.Event()
    client = make_client(producer=FakeProducer(gate=gate))
    job_id = client.post("/api/generate", json={"prompt": "Explain gradients"}).json()["job_id"]
    try:
        body = poll_job(client, job_id, statuses=("rendering",))
        assert body["stage_index"] == 3
        assert body["progress_stage"] == "Rendering your video"
        assert body["detail"] == "Rendering attempt 1"
        assert body["video_url"] is None and body["download_url"] is None
        assert body["title"] == "How Self-Attention Works"  # storyboard exists before rendering
        assert client.get(f"/api/jobs/{job_id}/video").status_code == 404
        # The latest attempt's code is already inspectable.
        code = client.get(f"/api/jobs/{job_id}/code")
        assert code.status_code == 200 and code.headers["x-code-source"] == "attempt-1"
    finally:
        gate.set()
    assert poll_job(client, job_id)["status"] == "completed"


def test_stage_index_never_decreases_over_polls(make_client, api_key):
    gate = threading.Event()
    client = make_client(producer=FakeProducer(gate=gate, failed_attempts=1))
    job_id = client.post("/api/generate", json={"prompt": "x"}).json()["job_id"]
    seen = []

    def poll():
        body = client.get(f"/api/jobs/{job_id}").json()
        seen.append(body["stage_index"])
        if body["status"] == "rendering":
            gate.set()
        return body["status"] in ("completed", "failed")

    wait_for(poll)
    assert seen == sorted(seen)
    assert seen[-1] == 4


def test_planner_failure_is_reported(make_client, api_key):
    planner = FakePlanner(error=PipelineError("The AI couldn't plan a lesson for that. Try rephrasing.", detail="validation: scenes"))
    client = make_client(planner=planner)
    job_id = client.post("/api/generate", json={"prompt": "x"}).json()["job_id"]
    body = poll_job(client, job_id)
    assert body["status"] == "failed"
    assert body["error"] == "The AI couldn't plan a lesson for that. Try rephrasing."
    assert body["video_url"] is None
    assert body["progress_stage"] == STAGE_LABELS[body["stage_index"]]
    assert "error_detail" not in body  # debug-only
    debug = client.get(f"/api/jobs/{job_id}/debug").json()
    assert "validation: scenes" in debug["error_detail"]
    assert client.get(f"/api/jobs/{job_id}/video").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/code").status_code == 404


def test_producer_failure_after_retries_is_reported(make_client, api_key):
    error = PipelineError("We couldn't render this animation. Please try again.", detail="render failed 3x")
    client = make_client(producer=FakeProducer(error=error, failed_attempts=3))
    job_id = client.post("/api/generate", json={"prompt": "x"}).json()["job_id"]
    body = poll_job(client, job_id)
    assert body["status"] == "failed"
    assert body["error"] == "We couldn't render this animation. Please try again."
    assert body["stage_index"] == 3
    assert body["attempts"] == 3
    debug = client.get(f"/api/jobs/{job_id}/debug").json()
    assert len(debug["attempt_logs"]) == 3
    assert "Traceback: boom" in debug["attempt_logs"][-1]["render_log_tail"]


def test_unexpected_exception_is_generic(make_client, api_key):
    client = make_client(producer=FakeProducer(error=ZeroDivisionError("internal detail")))
    job_id = client.post("/api/generate", json={"prompt": "x"}).json()["job_id"]
    body = poll_job(client, job_id)
    assert body["status"] == "failed"
    assert "internal detail" not in body["error"]
    assert body["error"].startswith("Something went wrong")


def test_offline_demo_is_labelled_and_needs_no_key(make_client, tmp_path, monkeypatch):
    d = tmp_path / "offline_demo"
    d.mkdir()
    (d / "storyboard.json").write_text(make_storyboard("How Gradient Descent Works").model_dump_json())
    (d / "scene.py").write_text("# demo\n")
    monkeypatch.setattr(offline_demo, "OFFLINE_DEMO_DIR", d)
    planner = FakePlanner()
    client = make_client(planner=planner)

    assert client.get("/api/health").json()["offline_demo_available"] is True
    res = client.post("/api/generate", json={"prompt": "Explain black holes", "mode": "offline_demo"})
    assert res.status_code == 200
    body = poll_job(client, res.json()["job_id"])
    assert body["status"] == "completed"
    assert body["mode"] == "offline_demo"
    assert body["prompt"] == "Explain black holes"
    assert body["title"] == "How Gradient Descent Works"
    assert planner.calls == []


def test_unknown_job_is_404(make_client):
    client = make_client()
    for suffix in ("", "/video", "/code", "/debug"):
        assert client.get(f"/api/jobs/abcdefghij{suffix}").status_code == 404
        assert client.get(f"/api/jobs/..%2F..%2Fetc{suffix}").status_code == 404
    assert client.get("/api/jobs/abcdefghij").json()["detail"].startswith("Job not found")


# --- health / CORS ---------------------------------------------------------------------------------


def test_health_tolerates_missing_renderer(make_client, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.pipeline.renderer", None)
    body = make_client().get("/api/health").json()
    assert body["ok"] is True
    assert body["anthropic_configured"] is False
    assert body["planner_model"] and body["codegen_model"]
    assert body["sandbox"]["docker_available"] is False
    assert "renderer module not available" in body["sandbox"]["error"]
    assert isinstance(body["offline_demo_available"], bool)


def test_health_uses_sandbox_status(make_client, api_key, monkeypatch):
    import types

    fake = types.ModuleType("app.pipeline.renderer")
    fake.sandbox_status = lambda: {"docker_available": True, "image_present": True, "image": "img:1", "error": None}
    monkeypatch.setitem(sys.modules, "app.pipeline.renderer", fake)
    body = make_client().get("/api/health").json()
    assert body["anthropic_configured"] is True
    assert body["sandbox"] == {"docker_available": True, "image_present": True, "image": "img:1", "error": None}


def test_cors_allows_frontend_origin(make_client):
    res = make_client().options(
        "/api/generate",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"
