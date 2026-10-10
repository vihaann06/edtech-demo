"""Render generated Manim code inside an isolated Docker container.

The container is the security boundary for LLM-written code:

* no network (`--network none`), read-only root filesystem, all capabilities
  dropped, no-new-privileges, non-root user, pid/memory/CPU limits;
* the only host paths it can see are the design system (read-only, at
  /opt/explainer) and the attempt directory (read-write, at /work);
* the docker CLI itself is invoked with a scrubbed environment (PATH, HOME and
  DOCKER_* only), so API keys never reach the container or its logs.

Only one render runs at a time (Manim is CPU-bound; the host has 4 cores).
Generated code is never executed on the host: if Docker or the image is
missing we raise `SandboxUnavailableError`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.config import get_settings
from app.pipeline.types import PipelineError

log = logging.getLogger(__name__)

SCENE_FILE = "scene.py"
STORYBOARD_FILE = "storyboard.json"
LOG_FILE = "render.log"
CONTAINER_UID = "1000:1000"  # `manimuser` in the base image
HELPERS_MOUNT = "/opt/explainer"
WORK_MOUNT = "/work"

_render_lock = threading.Lock()
_status_cache: tuple[float, dict[str, Any]] | None = None
_STATUS_TTL_S = 5.0

_TAG_RE = re.compile(r"^(EXPLAINER_[A-Z_]+) (\{.*\})\s*$")
_ANIM_RE = re.compile(r"Animation (\d+) : Partial movie file written")


class ProductionError(PipelineError):
    """Rendering failed after all repairs, validation failed, or the sandbox is unavailable.

    Defined here (and re-exported by production.py, its documented home) so that the
    sandbox error below can subclass it without a circular import."""


class SandboxUnavailableError(ProductionError):
    """Docker or the renderer image is not available. Generated code is never run on the host."""


@dataclass
class RenderResult:
    success: bool
    video_path: Optional[Path]
    stdout: str  # combined stdout+stderr of the container (also in render.log)
    log_text: str  # alias kept for readability at call sites
    exit_code: Optional[int]
    timed_out: bool
    wall_time_s: float
    timeline: list[dict[str, Any]] = field(default_factory=list)
    summary: Optional[dict[str, Any]] = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    layout_fixes: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[dict[str, Any]] = None
    traceback: str = ""
    animations_rendered: int = 0
    command: list[str] = field(default_factory=list)

    @property
    def stderr(self) -> str:
        return self.log_text


# --------------------------------------------------------------------------------------
# Docker plumbing
# --------------------------------------------------------------------------------------


def _docker_path() -> Optional[str]:
    found = shutil.which("docker")
    if found:
        return found
    for candidate in (
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
        "/usr/bin/docker",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def docker_env() -> dict[str, str]:
    """Environment for the docker CLI: PATH, HOME and DOCKER_* only (never ANTHROPIC_*)."""
    env = {k: v for k, v in os.environ.items() if k.startswith("DOCKER_")}
    path = os.environ.get("PATH", "")
    extra = ["/usr/local/bin", "/opt/homebrew/bin", "/Applications/Docker.app/Contents/Resources/bin", "/usr/bin", "/bin"]
    env["PATH"] = ":".join([p for p in path.split(":") if p] + [p for p in extra if p not in path.split(":")])
    if os.environ.get("HOME"):
        env["HOME"] = os.environ["HOME"]
    return env


def _run_docker(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    docker = _docker_path()
    if docker is None:
        raise FileNotFoundError("docker CLI not found")
    return subprocess.run(
        [docker, *args], capture_output=True, text=True, timeout=timeout, env=docker_env(), stdin=subprocess.DEVNULL
    )


def sandbox_status(*, refresh: bool = False) -> dict[str, Any]:
    """{"docker_available", "image_present", "image", "error"} - cheap, cached for a few seconds."""
    global _status_cache
    now = time.monotonic()
    if not refresh and _status_cache is not None and now - _status_cache[0] < _STATUS_TTL_S:
        return dict(_status_cache[1])
    image = get_settings().renderer_image
    status: dict[str, Any] = {"docker_available": False, "image_present": False, "image": image, "error": None}
    try:
        ver = _run_docker(["version", "--format", "{{.Server.Version}}"], timeout=8)
        if ver.returncode != 0:
            status["error"] = "Docker is installed but the daemon is not reachable. Start Docker Desktop."
        else:
            status["docker_available"] = True
            insp = _run_docker(["image", "inspect", "--format", "{{.Id}}", image], timeout=8)
            if insp.returncode == 0:
                status["image_present"] = True
            else:
                status["error"] = (
                    f"Renderer image {image} not found. Build it with: "
                    f"docker build -f docker/Dockerfile.renderer -t {image} docker/"
                )
    except FileNotFoundError:
        status["error"] = "Docker is not installed (docker CLI not found)."
    except subprocess.TimeoutExpired:
        status["error"] = "Docker did not respond in time."
    except Exception as exc:  # pragma: no cover - defensive
        status["error"] = f"Could not query Docker: {exc}"
    _status_cache = (now, status)
    return dict(status)


def ensure_sandbox() -> None:
    """Raise SandboxUnavailableError unless Docker and the renderer image are ready."""
    st = sandbox_status()
    if not (st["docker_available"] and st["image_present"]):
        st = sandbox_status(refresh=True)
    if not st["docker_available"] or not st["image_present"]:
        raise SandboxUnavailableError(
            "The video renderer isn't available right now, so no video could be made.",
            detail=st.get("error") or "docker/image unavailable",
        )


def build_command(
    attempt_dir: Path, container_name: str, *, with_storyboard: bool, width: int, height: int, fps: int
) -> list[str]:
    """The full `docker run ...` argv (without the docker binary)."""
    s = get_settings()
    helpers = s.manim_helpers_dir.resolve()
    cmd = [
        "run", "--rm",
        "--name", container_name,
        "--network", "none",
        "--memory", s.render_memory,
        "--memory-swap", s.render_memory,
        "--cpus", str(s.render_cpus),
        "--pids-limit", "512",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=1g,mode=1777",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", CONTAINER_UID,
        "-e", "HOME=/tmp",
        "-e", f"PYTHONPATH={HELPERS_MOUNT}",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "MPLCONFIGDIR=/tmp",
        "-v", f"{helpers}:{HELPERS_MOUNT}:ro",
        "-v", f"{attempt_dir.resolve()}:{WORK_MOUNT}:rw",
        "-w", WORK_MOUNT,
    ]
    if with_storyboard:
        cmd += ["-e", f"EXPLAINER_STORYBOARD={WORK_MOUNT}/{STORYBOARD_FILE}"]
    cmd += [
        s.renderer_image,
        "manim", "render", SCENE_FILE, "ExplainerVideo",
        "--resolution", f"{width},{height}",
        "--fps", str(fps),
        "--disable_caching",
        "--media_dir", f"{WORK_MOUNT}/media",
        "--progress_bar", "none",
        "--format", "mp4",
        "-o", "ExplainerVideo",
        "-v", "INFO",
    ]
    return cmd


def _kill(container_name: str) -> None:
    try:
        _run_docker(["kill", container_name], timeout=20)
    except Exception:  # pragma: no cover - best effort
        pass


# --------------------------------------------------------------------------------------
# Output parsing
# --------------------------------------------------------------------------------------


def parse_tagged_lines(text: str) -> dict[str, list[dict[str, Any]]]:
    """Collect EXPLAINER_* JSON lines printed by visual_helpers, keyed by tag."""
    out: dict[str, list[dict[str, Any]]] = {}
    for line in text.splitlines():
        m = _TAG_RE.match(line.strip())
        if not m:
            continue
        try:
            payload = json.loads(m.group(2))
        except json.JSONDecodeError:
            continue
        out.setdefault(m.group(1), []).append(payload)
    return out


def parse_timeline(text: str) -> list[dict[str, Any]]:
    return parse_tagged_lines(text).get("EXPLAINER_TIMELINE", [])


def extract_traceback(text: str) -> str:
    """The plain Python traceback printed by ExplainerScene.render, or a cleaned log tail."""
    start = text.rfind("EXPLAINER_TRACEBACK_BEGIN")
    if start != -1:
        end = text.find("EXPLAINER_TRACEBACK_END", start)
        body = text[start + len("EXPLAINER_TRACEBACK_BEGIN"): end if end != -1 else None]
        return body.strip()
    # Errors before the scene starts (import errors, bad class names) come from Manim's
    # own (rich) traceback: strip box-drawing characters and keep the tail.
    cleaned = []
    for line in text.splitlines():
        line = re.sub(r"[─-╿▀-▟]", " ", line).rstrip()
        if line.strip():
            cleaned.append(line)
    return "\n".join(cleaned[-60:])


def find_video(attempt_dir: Path) -> Optional[Path]:
    media = attempt_dir / "media" / "videos"
    if not media.exists():
        return None
    candidates = [p for p in media.rglob("ExplainerVideo.mp4") if "partial_movie_files" not in p.parts]
    if not candidates:
        candidates = [p for p in media.rglob("*.mp4") if "partial_movie_files" not in p.parts]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------------------


def render(
    code: str,
    attempt_dir: Path,
    *,
    timeout_s: float | None = None,
    on_log: Optional[Callable[[str], None]] = None,
    storyboard_json: str | None = None,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
    on_queue: Optional[Callable[[], None]] = None,
) -> RenderResult:
    """Render `code` (defining class ExplainerVideo) in the sandbox.

    Writes attempt_dir/scene.py (+ storyboard.json when given, which ExplainerScene
    uses as the source of truth for captions and timing), streams container output
    to attempt_dir/render.log and `on_log`, and returns a RenderResult. Raises
    SandboxUnavailableError if Docker/the image is missing.
    """
    s = get_settings()
    ensure_sandbox()
    attempt_dir = Path(attempt_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / SCENE_FILE).write_text(code, encoding="utf-8")
    if storyboard_json is not None:
        (attempt_dir / STORYBOARD_FILE).write_text(storyboard_json, encoding="utf-8")
    timeout = float(timeout_s if timeout_s is not None else s.render_timeout_s)
    w, h, f = width or s.render_width, height or s.render_height, fps or s.render_fps
    name = f"explainer-render-{uuid.uuid4().hex[:12]}"
    argv = build_command(attempt_dir, name, with_storyboard=storyboard_json is not None, width=w, height=h, fps=f)
    docker = _docker_path()
    if docker is None:  # pragma: no cover - ensure_sandbox would have raised
        raise SandboxUnavailableError("The video renderer isn't available right now.", detail="docker CLI not found")

    if not _render_lock.acquire(blocking=False):
        if on_queue:
            on_queue()
        _render_lock.acquire()
    try:
        return _run(argv, docker, name, attempt_dir, timeout, on_log)
    finally:
        _render_lock.release()


def _run(argv: list[str], docker: str, name: str, attempt_dir: Path, timeout: float,
         on_log: Optional[Callable[[str], None]]) -> RenderResult:
    log_path = attempt_dir / LOG_FILE
    lines: list[str] = []
    started = time.monotonic()
    timed_out = False
    with open(log_path, "w", encoding="utf-8") as log_fh:
        log_fh.write("$ docker " + " ".join(argv) + "\n")
        log_fh.flush()
        proc = subprocess.Popen(
            [docker, *argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, bufsize=1, env=docker_env(), errors="replace",
        )

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line)
                log_fh.write(line)
                log_fh.flush()
                if on_log:
                    try:
                        on_log(line.rstrip("\n"))
                    except Exception:  # pragma: no cover - callbacks must not break rendering
                        log.exception("on_log callback failed")

        reader = threading.Thread(target=pump, name=f"{name}-log", daemon=True)
        reader.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill(name)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:  # pragma: no cover
                proc.kill()
                proc.wait()
        reader.join(timeout=10)
        if timed_out:
            msg = f"\nRENDER TIMEOUT: killed after {timeout:.0f}s\n"
            lines.append(msg)
            log_fh.write(msg)
    wall = time.monotonic() - started
    text = "".join(lines)
    tags = parse_tagged_lines(text)
    video = find_video(attempt_dir)
    exit_code = proc.returncode
    success = (not timed_out) and exit_code == 0 and video is not None and video.stat().st_size > 0
    errors = tags.get("EXPLAINER_ERROR", [])
    summary = tags.get("EXPLAINER_SUMMARY", [None])[-1]
    anim_numbers = [int(m.group(1)) for m in _ANIM_RE.finditer(text)]
    return RenderResult(
        success=success,
        video_path=video if success else None,
        stdout=text,
        log_text=text,
        exit_code=exit_code,
        timed_out=timed_out,
        wall_time_s=round(wall, 2),
        timeline=tags.get("EXPLAINER_TIMELINE", []),
        summary=summary,
        warnings=tags.get("EXPLAINER_WARNING", []),
        layout_fixes=tags.get("EXPLAINER_LAYOUT", []),
        error=errors[-1] if errors else None,
        traceback=extract_traceback(text) if not success else "",
        animations_rendered=(max(anim_numbers) + 1) if anim_numbers else 0,
        command=["docker", *argv],
    )
