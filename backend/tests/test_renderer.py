"""Tests for the Docker sandbox renderer (unit tests + docker-marked sandbox checks)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app.config import BACKEND_DIR
from app.pipeline import renderer
from app.pipeline.renderer import SandboxUnavailableError

SAMPLE_LOG = """\
INFO     Animation 0 : Partial movie file written in '/work/media/x/uncached_00000.mp4'
EXPLAINER_BLOCK_START {"index":0,"total":2}
EXPLAINER_TIMELINE {"index":0,"planned":7.5,"actual":7.5,"start":0.0,"end":7.5,"clamped_calls":1}
EXPLAINER_LAYOUT {"index":0,"reason":"play","scale":1.0,"dx":0.0,"dy":0.4}
some noise EXPLAINER_TIMELINE {"not": "at line start"}
EXPLAINER_TIMELINE {broken json
EXPLAINER_TIMELINE {"index":1,"planned":6.0,"actual":6.0,"start":7.5,"end":13.5,"clamped_calls":0}
EXPLAINER_SUMMARY {"frames":405,"fps":30.0,"duration":13.5}
"""


# --------------------------------------------------------------------------------------
# Unit tests (no Docker)
# --------------------------------------------------------------------------------------


def test_parse_timeline_and_tags():
    tl = renderer.parse_timeline(SAMPLE_LOG)
    assert [e["index"] for e in tl] == [0, 1]
    assert tl[0]["planned"] == 7.5 and tl[0]["clamped_calls"] == 1
    tags = renderer.parse_tagged_lines(SAMPLE_LOG)
    assert tags["EXPLAINER_SUMMARY"][0]["frames"] == 405
    assert tags["EXPLAINER_LAYOUT"][0]["dy"] == 0.4
    assert len(tags["EXPLAINER_BLOCK_START"]) == 1


def test_extract_traceback_prefers_markers_and_falls_back_to_clean_tail():
    log = "noise\nEXPLAINER_TRACEBACK_BEGIN\nTraceback (most recent call last):\n  File \"/work/scene.py\", line 9, in construct\nNameError: name 'x' is not defined\nEXPLAINER_TRACEBACK_END\nmore"
    tb = renderer.extract_traceback(log)
    assert tb.startswith("Traceback") and tb.endswith("is not defined")
    rich = "╭──── Traceback ────╮\n│ scene.py:3 │\n╰───────────────────╯\nModuleNotFoundError: No module named 'foo'"
    out = renderer.extract_traceback(rich)
    assert "ModuleNotFoundError" in out and "╭" not in out


def test_build_command_has_every_sandbox_flag(tmp_path):
    cmd = renderer.build_command(tmp_path, "explainer-render-test", with_storyboard=True, width=1920, height=1080, fps=30)
    joined = " ".join(cmd)
    for flag in ["--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--pids-limit 512", "--user 1000:1000", "-e HOME=/tmp", "--rm", "--name explainer-render-test",
                 "--disable_caching", "--resolution 1920,1080", "--fps 30", "-e EXPLAINER_STORYBOARD=/work/storyboard.json"]:
        assert flag in joined, flag
    assert "--memory 3g" in joined and "--memory-swap 3g" in joined and "--cpus 3" in joined
    helpers = str((BACKEND_DIR / "manim").resolve())
    assert f"{helpers}:/opt/explainer:ro" in cmd
    assert f"{tmp_path.resolve()}:/work:rw" in cmd
    assert cmd[cmd.index("--tmpfs") + 1].startswith("/tmp:")
    assert "ANTHROPIC" not in joined


def test_docker_env_is_scrubbed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    env = renderer.docker_env()
    assert set(env) <= {"PATH", "HOME"} | {k for k in env if k.startswith("DOCKER_")}
    assert env["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert "sk-secret" not in json.dumps(env)


def test_find_video_ignores_partial_movies(tmp_path):
    partial = tmp_path / "media" / "videos" / "scene" / "1080p30" / "partial_movie_files" / "ExplainerVideo"
    partial.mkdir(parents=True)
    (partial / "uncached_00000.mp4").write_bytes(b"x")
    assert renderer.find_video(tmp_path) is None
    final = tmp_path / "media" / "videos" / "scene" / "1080p30" / "ExplainerVideo.mp4"
    final.write_bytes(b"video")
    assert renderer.find_video(tmp_path) == final


def test_sandbox_status_is_cached_and_reports_errors(monkeypatch):
    calls = []

    def fake_run(args, timeout):
        calls.append(args)
        rc = 0 if args[0] == "version" else 1
        return subprocess.CompletedProcess(args, rc, stdout="27.0", stderr="")

    monkeypatch.setattr(renderer, "_run_docker", fake_run)
    renderer._status_cache = None
    st = renderer.sandbox_status()
    assert st["docker_available"] is True and st["image_present"] is False
    assert "docker build" in st["error"]
    n = len(calls)
    renderer.sandbox_status()
    assert len(calls) == n  # served from cache
    renderer.sandbox_status(refresh=True)
    assert len(calls) == 2 * n
    renderer._status_cache = None


def test_unavailable_docker_raises_and_never_runs_code_on_host(monkeypatch, tmp_path):
    def no_docker(args, timeout):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(renderer, "_run_docker", no_docker)
    renderer._status_cache = None
    st = renderer.sandbox_status(refresh=True)
    assert st["docker_available"] is False and "not installed" in st["error"]
    with pytest.raises(SandboxUnavailableError) as exc:
        renderer.render("raise SystemExit('must not run')", tmp_path)
    assert "renderer" in exc.value.user_message.lower()
    assert not (tmp_path / "scene.py").exists()
    renderer._status_cache = None


# --------------------------------------------------------------------------------------
# Docker tests
# --------------------------------------------------------------------------------------


def _sandbox_ready() -> bool:
    try:
        renderer._status_cache = None
        st = renderer.sandbox_status(refresh=True)
    except Exception:
        return False
    return bool(st.get("docker_available") and st.get("image_present"))


@pytest.fixture(scope="module")
def sandbox():
    if not _sandbox_ready():
        pytest.skip("Docker or the renderer image is not available")


@pytest.fixture
def work_dir(request, sandbox):
    """Attempt dirs must live under /Users for Docker Desktop file sharing."""
    d = BACKEND_DIR / ".jobs" / f"pytest-agent3-{request.node.name}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


HEADER = "from manim import *\nfrom visual_helpers import *\n\n"


@pytest.mark.docker
def test_minimal_render_has_exact_timing(work_dir):
    code = HEADER + (
        "class ExplainerVideo(ExplainerScene):\n    def construct(self):\n"
        "        with self.scene_block(index=0, concept='One', caption='First block of the test.', duration=1.5):\n"
        "            self.play(Create(Square()), run_time=0.7)\n"
        "        with self.scene_block(index=1, concept='Two', caption='Second block of the test.', duration=1.0):\n"
        "            self.play(FadeIn(Circle()), run_time=3.0)\n"  # overruns: must be clamped
    )
    lines = []
    r = renderer.render(code, work_dir, width=640, height=360, fps=30, timeout_s=180, on_log=lines.append)
    assert r.success, r.log_text[-3000:]
    assert r.video_path and r.video_path.stat().st_size > 0
    assert (work_dir / "render.log").read_text().count("EXPLAINER_TIMELINE") == 2
    assert [e["index"] for e in r.timeline] == [0, 1]
    assert r.timeline[0]["actual"] == 1.5 and r.timeline[1]["actual"] == 1.0
    assert r.timeline[1]["clamped_calls"] == 1
    assert r.summary["frames"] == 75
    assert any("Partial movie file written" in line for line in lines)
    assert r.animations_rendered >= 2


@pytest.mark.docker
def test_sandbox_has_no_network_no_host_files_no_secrets(work_dir, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-should-never-leak")
    code = HEADER + "import socket, os, json\n\n" + (
        "class ExplainerVideo(ExplainerScene):\n    def construct(self):\n"
        "        r = {}\n"
        "        try:\n            socket.create_connection(('1.1.1.1', 53), timeout=3); r['tcp'] = 'CONNECTED'\n"
        "        except Exception as e:\n            r['tcp'] = 'blocked'\n"
        "        try:\n            socket.gethostbyname('example.com'); r['dns'] = 'RESOLVED'\n"
        "        except Exception as e:\n            r['dns'] = 'blocked'\n"
        "        r['users'] = os.path.exists('/Users')\n"
        "        r['secrets'] = [k for k in os.environ if 'ANTHROPIC' in k]\n"
        "        r['uid'] = os.getuid()\n"
        "        for name, path in [('rootfs', '/etc/probe'), ('helpers', '/opt/explainer/probe')]:\n"
        "            try:\n                open(path, 'w').write('x'); r[name] = 'WRITABLE'\n"
        "            except Exception:\n                r[name] = 'denied'\n"
        "        print('SANDBOX_PROBE ' + json.dumps(r), flush=True)\n"
        "        with self.scene_block(index=0, concept='Probe', caption='Sandbox probe.', duration=0.5):\n"
        "            self.play(FadeIn(Dot()))\n"
    )
    r = renderer.render(code, work_dir, width=320, height=180, fps=15, timeout_s=180)
    assert r.success, r.log_text[-3000:]
    line = next(l for l in r.log_text.splitlines() if l.startswith("SANDBOX_PROBE "))
    probe = json.loads(line.split(" ", 1)[1])
    assert probe == {"tcp": "blocked", "dns": "blocked", "users": False, "secrets": [], "uid": 1000,
                     "rootfs": "denied", "helpers": "denied"}
    assert "sk-test-should-never-leak" not in r.log_text


@pytest.mark.docker
def test_runtime_error_is_captured_with_plain_traceback(work_dir):
    code = HEADER + (
        "class ExplainerVideo(ExplainerScene):\n    def construct(self):\n"
        "        with self.scene_block(index=0, concept='Boom', caption='This scene fails.', duration=1.0):\n"
        "            self.play(Create(undefined_thing))\n"
    )
    r = renderer.render(code, work_dir, width=320, height=180, fps=15, timeout_s=180)
    assert not r.success and r.exit_code != 0 and r.video_path is None
    assert "NameError" in r.traceback and 'scene.py", line 7' in r.traceback
    assert r.error and r.error["type"] == "NameError"


@pytest.mark.docker
def test_timeout_kills_the_container(work_dir):
    code = HEADER + "class ExplainerVideo(ExplainerScene):\n    def construct(self):\n        while True:\n            pass\n"
    started = time.monotonic()
    r = renderer.render(code, work_dir, width=320, height=180, fps=15, timeout_s=8)
    assert r.timed_out and not r.success
    assert time.monotonic() - started < 60
    ps = subprocess.run(["docker", "ps", "-q", "--filter", "name=explainer-render"], capture_output=True, text=True,
                        env=renderer.docker_env())
    assert ps.stdout.strip() == ""
