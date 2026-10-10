"""ffprobe/ffmpeg checks on rendered videos.

Uses the host's ffprobe/ffmpeg when they are on PATH; otherwise runs them inside the
renderer image (no network, read-only, the video's directory mounted read-only).
A video that fails any check raises `VideoValidationError` - a bad video is never
reported as a success.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings
from app.pipeline import renderer
from app.pipeline.types import PipelineError

_DECODE_MARK = "@@EXPLAINER_DECODE@@"


class VideoValidationError(PipelineError):
    """The rendered video is missing, unreadable, or does not match the spec."""

    def __init__(self, user_message: str, *, detail: str | None = None, kind: str = "invalid") -> None:
        super().__init__(user_message, detail=detail)
        # missing | empty | probe | no_video | codec | pix_fmt | decode | duration | size
        self.kind = kind


@dataclass
class VideoInfo:
    duration_s: float
    width: int
    height: int
    fps: float
    codec: str
    size_bytes: int
    pix_fmt: str = ""
    nb_frames: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_rate(rate: str | None) -> float:
    if not rate:
        return 0.0
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            return float(num) / float(den) if float(den) else 0.0
        return float(rate)
    except ValueError:
        return 0.0


def _host_tools() -> bool:
    return bool(shutil.which("ffprobe") and shutil.which("ffmpeg"))


def _probe_and_decode(path: Path, *, decode: bool, timeout: float = 120) -> tuple[dict[str, Any], str]:
    """Return (ffprobe JSON, decode error text). Decode text is '' when clean or not requested."""
    probe_args = ["-v", "error", "-print_format", "json", "-show_streams", "-show_format"]
    if _host_tools():
        p = subprocess.run(["ffprobe", *probe_args, str(path)], capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise VideoValidationError("The rendered video could not be read.", detail=p.stderr[-2000:], kind="probe")
        errors = ""
        if decode:
            d = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"],
                               capture_output=True, text=True, timeout=timeout)
            errors = (d.stderr or "").strip() or ("" if d.returncode == 0 else f"ffmpeg exited {d.returncode}")
        return json.loads(p.stdout or "{}"), errors

    renderer.ensure_sandbox()
    docker = renderer._docker_path()
    image = get_settings().renderer_image
    name = path.name
    script = f'ffprobe {" ".join(probe_args)} "/in/{name}" || exit 3; echo "{_DECODE_MARK}"; '
    if decode:
        script += f'ffmpeg -v error -nostdin -i "/in/{name}" -f null - 2>&1; echo "@@RC=$?"'
    cmd = [
        docker, "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", renderer.CONTAINER_UID,
        "--memory", "1g", "--pids-limit", "128",
        "-v", f"{path.parent.resolve()}:/in:ro", image, "sh", "-c", script,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=renderer.docker_env(),
                       stdin=subprocess.DEVNULL)
    out = p.stdout or ""
    if p.returncode == 3 or _DECODE_MARK not in out:
        raise VideoValidationError("The rendered video could not be read.",
                                   detail=(p.stderr or out)[-2000:], kind="probe")
    probe_text, _, decode_text = out.partition(_DECODE_MARK)
    errors = ""
    if decode:
        body = decode_text.strip()
        rc = 0
        if "@@RC=" in body:
            body, _, rc_text = body.rpartition("@@RC=")
            try:
                rc = int(rc_text.strip() or 0)
            except ValueError:
                rc = 1
        body = body.strip()
        errors = body or ("" if rc == 0 else f"ffmpeg exited {rc}")
    return json.loads(probe_text or "{}"), errors


def _info_from_probe(data: dict[str, Any], path: Path) -> VideoInfo:
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise VideoValidationError("The rendered file has no video in it.", detail=json.dumps(data)[:2000],
                                   kind="no_video")
    v = streams[0]
    fmt = data.get("format", {})
    duration = 0.0
    for candidate in (fmt.get("duration"), v.get("duration")):
        try:
            if candidate is not None and float(candidate) > 0:
                duration = float(candidate)
                break
        except (TypeError, ValueError):
            continue
    nb = v.get("nb_frames")
    try:
        nb_frames = int(nb) if nb is not None else None
    except ValueError:
        nb_frames = None
    return VideoInfo(
        duration_s=round(duration, 3),
        width=int(v.get("width") or 0),
        height=int(v.get("height") or 0),
        fps=round(_parse_rate(v.get("avg_frame_rate")) or _parse_rate(v.get("r_frame_rate")), 3),
        codec=str(v.get("codec_name") or ""),
        size_bytes=path.stat().st_size,
        pix_fmt=str(v.get("pix_fmt") or ""),
        nb_frames=nb_frames,
    )


def _check_file(path: Path) -> None:
    if not path.exists():
        raise VideoValidationError("The renderer finished but no video file was produced.", detail=str(path),
                                   kind="missing")
    if path.stat().st_size == 0:
        raise VideoValidationError("The rendered video file is empty.", detail=str(path), kind="empty")


def probe_video(path: Path | str) -> VideoInfo:
    """Read duration/size/fps/codec with ffprobe."""
    path = Path(path)
    _check_file(path)
    data, _ = _probe_and_decode(path, decode=False)
    return _info_from_probe(data, path)


def validate_video(
    path: Path | str,
    *,
    expected_duration: float | None = None,
    tolerance: float | None = None,
    expected_size: tuple[int, int] | None = None,
    check_decode: bool = True,
) -> VideoInfo:
    """Probe + decode the video and check it against the spec. Raises VideoValidationError."""
    s = get_settings()
    path = Path(path)
    expected_duration = s.target_duration_s if expected_duration is None else expected_duration
    tolerance = s.duration_tolerance_s if tolerance is None else tolerance
    expected_size = (s.render_width, s.render_height) if expected_size is None else expected_size

    _check_file(path)
    data, decode_errors = _probe_and_decode(path, decode=check_decode)
    info = _info_from_probe(data, path)
    if info.codec != "h264":
        raise VideoValidationError("The video was encoded in an unexpected format.",
                                   detail=f"codec={info.codec}, expected h264", kind="codec")
    if info.pix_fmt and info.pix_fmt != "yuv420p":
        raise VideoValidationError("The video uses a pixel format browsers can't play.",
                                   detail=f"pix_fmt={info.pix_fmt}, expected yuv420p", kind="pix_fmt")
    if decode_errors:
        raise VideoValidationError("The rendered video is corrupted.", detail=decode_errors[-2000:], kind="decode")
    if (info.width, info.height) != tuple(expected_size):
        raise VideoValidationError(
            "The video came out at the wrong resolution.",
            detail=f"{info.width}x{info.height}, expected {expected_size[0]}x{expected_size[1]}", kind="size",
        )
    if abs(info.duration_s - expected_duration) > tolerance:
        raise VideoValidationError(
            "The video came out at the wrong length.",
            detail=f"duration {info.duration_s:.2f}s, expected {expected_duration:.1f}s +/- {tolerance:.1f}s",
            kind="duration",
        )
    return info
