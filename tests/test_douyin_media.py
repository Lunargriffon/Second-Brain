from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from pkb.douyin.media import (
    AcquisitionDisposition,
    TemporaryMedia,
    classify_acquisition_failure,
)


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")


def test_prepare_creates_a_dedicated_work_directory(tmp_path: Path) -> None:
    media = TemporaryMedia(tmp_path / "douyin")

    paths = media.prepare("123")

    assert paths.work_dir == (tmp_path / "douyin" / "123").resolve()
    assert paths.video == paths.work_dir / "video.mp4"
    assert paths.audio == paths.work_dir / "audio.wav"
    assert paths.work_dir.is_dir()


def test_prepare_rejects_work_id_that_can_escape_root(tmp_path: Path) -> None:
    media = TemporaryMedia(tmp_path / "douyin")

    with pytest.raises(ValueError, match="work ID"):
        media.prepare("../outside")


def test_extract_audio_uses_mono_16khz(tmp_path: Path) -> None:
    runner = RecordingRunner()
    media = TemporaryMedia(tmp_path / "douyin", runner=runner)
    paths = media.prepare("123")
    paths.video.write_bytes(b"video")

    media.extract_audio(paths)

    command, options = runner.calls[-1]
    assert command[-7:] == ["-vn", "-ac", "1", "-ar", "16000", "-y", str(paths.audio)]
    assert options == {"check": True, "capture_output": True, "text": True}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("not_found", AcquisitionDisposition.UNAVAILABLE),
        ("http_410", AcquisitionDisposition.UNAVAILABLE),
        ("timeout", AcquisitionDisposition.RETRYABLE),
        ("http_503", AcquisitionDisposition.RETRYABLE),
        ("auth_required", AcquisitionDisposition.STOP_RUN),
        ("captcha", AcquisitionDisposition.STOP_RUN),
        ("http_403", AcquisitionDisposition.STOP_RUN),
        ("http_429", AcquisitionDisposition.STOP_RUN),
    ],
)
def test_acquisition_failures_are_classified(code: str, expected: AcquisitionDisposition) -> None:
    assert classify_acquisition_failure(code) is expected


def test_cleanup_removes_only_the_requested_work_directory(tmp_path: Path) -> None:
    media = TemporaryMedia(tmp_path / "root")
    first = media.prepare("1")
    second = media.prepare("2")
    first.video.write_bytes(b"video")

    media.cleanup(first.work_dir)

    assert not first.work_dir.exists()
    assert second.work_dir.is_dir()
    assert media.root.is_dir()


def test_cleanup_refuses_path_outside_root(tmp_path: Path) -> None:
    outside = tmp_path / "other"
    outside.mkdir()

    with pytest.raises(ValueError, match="temporary root"):
        TemporaryMedia(tmp_path / "root").cleanup(outside)


def test_cleanup_refuses_symlink_that_resolves_outside_root(tmp_path: Path) -> None:
    media = TemporaryMedia(tmp_path / "root")
    outside = tmp_path / "other"
    outside.mkdir()
    link = media.root / "escaped"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="temporary root"):
        media.cleanup(link)
    assert outside.is_dir()
