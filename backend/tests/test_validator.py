"""Tests for validator: ffprobe-based checks (unit tests fake ffprobe; docker tests use real files)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import BACKEND_DIR
from app.pipeline import renderer, validator
from app.pipeline.validator import VideoValidationError


def probe_json(*, duration="30.000000", width=1920, height=1080, codec="h264", pix_fmt="yuv420p", rate="30/1",
               streams=True) -> dict:
    data = {"format": {"duration": duration}, "streams": []}
    if streams:
        data["streams"].append({"codec_type": "video", "codec_name": codec, "width": width, "height": height,
                                "pix_fmt": pix_fmt, "avg_frame_rate": rate, "nb_frames": "900"})
    return data


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    p = tmp_path / "final.mp4"
    p.write_bytes(b"\x00" * 1024)
    return p


def fake_probe(monkeypatch, data: dict, decode_errors: str = "") -> None:
    monkeypatch.setattr(validator, "_probe_and_decode", lambda path, decode=True, timeout=120: (data, decode_errors))


def test_valid_video_passes(monkeypatch, video_file):
    fake_probe(monkeypatch, probe_json())
    info = validator.validate_video(video_file, expected_duration=30.0, tolerance=3.0, expected_size=(1920, 1080))
    assert (info.width, info.height, info.fps, info.codec, info.nb_frames) == (1920, 1080, 30.0, "h264", 900)
    assert info.duration_s == 30.0 and info.size_bytes == 1024


@pytest.mark.parametrize(
    "data, decode, kind",
    [
        (probe_json(duration="40.0"), "", "duration"),
        (probe_json(duration="12.5"), "", "duration"),
        (probe_json(width=1280, height=720), "", "size"),
        (probe_json(codec="vp9"), "", "codec"),
        (probe_json(pix_fmt="yuv444p"), "", "pix_fmt"),
        (probe_json(streams=False), "", "no_video"),
        (probe_json(), "[h264 @ 0x1] corrupt frame", "decode"),
    ],
)
def test_bad_videos_are_rejected(monkeypatch, video_file, data, decode, kind):
    fake_probe(monkeypatch, data, decode)
    with pytest.raises(VideoValidationError) as exc:
        validator.validate_video(video_file, expected_duration=30.0, tolerance=3.0, expected_size=(1920, 1080))
    assert exc.value.kind == kind
    assert exc.value.user_message  # UI-safe message


def test_duration_tolerance_boundary(monkeypatch, video_file):
    fake_probe(monkeypatch, probe_json(duration="33.0"))
    validator.validate_video(video_file, expected_duration=30.0, tolerance=3.0, expected_size=(1920, 1080))
    fake_probe(monkeypatch, probe_json(duration="33.2"))
    with pytest.raises(VideoValidationError):
        validator.validate_video(video_file, expected_duration=30.0, tolerance=3.0, expected_size=(1920, 1080))


def test_defaults_come_from_settings(monkeypatch, video_file):
    monkeypatch.setenv("RENDER_WIDTH", "1280")
    monkeypatch.setenv("RENDER_HEIGHT", "720")
    from app.config import get_settings

    get_settings.cache_clear()
    fake_probe(monkeypatch, probe_json(width=1280, height=720))
    assert validator.validate_video(video_file).width == 1280
    fake_probe(monkeypatch, probe_json())
    with pytest.raises(VideoValidationError):
        validator.validate_video(video_file)


def test_missing_and_empty_files(tmp_path):
    with pytest.raises(VideoValidationError) as exc:
        validator.validate_video(tmp_path / "nope.mp4")
    assert exc.value.kind == "missing"
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(VideoValidationError) as exc:
        validator.probe_video(empty)
    assert exc.value.kind == "empty"


# --------------------------------------------------------------------------------------
# Docker: real ffprobe inside the renderer image
# --------------------------------------------------------------------------------------


def _sandbox_ready() -> bool:
    try:
        st = renderer.sandbox_status(refresh=True)
    except Exception:
        return False
    return bool(st.get("docker_available") and st.get("image_present"))


@pytest.fixture(scope="module")
def rendered_clip():
    """A 2 s, 640x360 clip rendered in the sandbox (bind mounts must live under /Users)."""
    if not _sandbox_ready():
        pytest.skip("Docker or the renderer image is not available")
    work = BACKEND_DIR / ".jobs" / "pytest-agent3-validator"
    shutil.rmtree(work, ignore_errors=True)
    code = (
        "from manim import *\nfrom visual_helpers import *\n\n"
        "class ExplainerVideo(ExplainerScene):\n    def construct(self):\n"
        "        with self.scene_block(index=0, concept='Probe', caption='A short validation clip.', duration=2.0):\n"
        "            self.play(Create(Circle(color=BLUE)), run_time=1.0)\n"
    )
    result = renderer.render(code, work, width=640, height=360, fps=30, timeout_s=180)
    assert result.success, result.log_text[-3000:]
    yield result.video_path
    shutil.rmtree(work, ignore_errors=True)


@pytest.mark.docker
def test_real_probe_and_validate(rendered_clip):
    info = validator.probe_video(rendered_clip)
    assert (info.width, info.height, info.codec, info.pix_fmt) == (640, 360, "h264", "yuv420p")
    assert abs(info.duration_s - 2.0) < 0.05 and info.nb_frames == 60
    validator.validate_video(rendered_clip, expected_duration=2.0, tolerance=0.1, expected_size=(640, 360))
    with pytest.raises(VideoValidationError) as exc:
        validator.validate_video(rendered_clip, expected_duration=30.0, tolerance=3.0, expected_size=(640, 360))
    assert exc.value.kind == "duration"
    with pytest.raises(VideoValidationError) as exc:
        validator.validate_video(rendered_clip, expected_duration=2.0, tolerance=0.1, expected_size=(1920, 1080))
    assert exc.value.kind == "size"


@pytest.mark.docker
def test_real_corrupt_file_is_rejected(rendered_clip):
    bad_dir = BACKEND_DIR / ".jobs" / "pytest-agent3-validator-bad"
    bad_dir.mkdir(parents=True, exist_ok=True)
    try:
        data = rendered_clip.read_bytes()
        bad = bad_dir / "truncated.mp4"
        bad.write_bytes(data[: len(data) // 3])
        with pytest.raises(VideoValidationError):
            validator.validate_video(bad, expected_duration=2.0, tolerance=0.1, expected_size=(640, 360))
        junk = bad_dir / "junk.mp4"
        junk.write_bytes(b"not a video at all" * 100)
        with pytest.raises(VideoValidationError) as exc:
            validator.validate_video(junk, expected_duration=2.0, tolerance=0.1, expected_size=(640, 360))
        assert exc.value.kind in {"probe", "no_video", "decode"}
    finally:
        shutil.rmtree(bad_dir, ignore_errors=True)
