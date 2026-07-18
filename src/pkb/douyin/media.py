"""Safe, bounded temporary media handling for Douyin ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]


class AcquisitionDisposition(str, Enum):
    """The action a pipeline should take after media acquisition fails."""

    UNAVAILABLE = "unavailable"
    RETRYABLE = "retryable"
    STOP_RUN = "stop_run"


class AcquisitionFailure(RuntimeError):
    """A safe acquisition error carrying only a stable code and disposition."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.disposition = classify_acquisition_failure(code)
        super().__init__(code)


def classify_acquisition_failure(code: str) -> AcquisitionDisposition:
    """Classify a safe acquisition error code without inspecting response bodies."""

    normalized = code.strip().lower()
    if normalized in {"not_found", "unavailable", "http_404", "http_410"}:
        return AcquisitionDisposition.UNAVAILABLE
    if normalized in {
        "auth_required",
        "captcha",
        "http_401",
        "http_403",
        "http_429",
    }:
        return AcquisitionDisposition.STOP_RUN
    return AcquisitionDisposition.RETRYABLE


@dataclass(frozen=True)
class MediaPaths:
    work_dir: Path
    video: Path
    audio: Path


class TemporaryMedia:
    """Own work directories below one dedicated temporary root."""

    def __init__(
        self,
        root: Path,
        *,
        runner: Runner = subprocess.run,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()
        self.runner = runner
        self.ffmpeg = ffmpeg

    def prepare(self, work_id: str) -> MediaPaths:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", work_id):
            raise ValueError("invalid work ID")
        work_dir = self.root / work_id
        work_dir.mkdir(parents=False, exist_ok=True)
        resolved = self._require_work_dir(work_dir)
        return MediaPaths(
            work_dir=resolved,
            video=resolved / "video.mp4",
            audio=resolved / "audio.wav",
        )

    def extract_audio(self, paths: MediaPaths) -> None:
        work_dir = self._require_work_dir(paths.work_dir)
        video = paths.video.resolve()
        audio = paths.audio.resolve()
        if video.parent != work_dir or audio.parent != work_dir:
            raise ValueError("media path must remain within work directory")
        command = [
            self.ffmpeg,
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-y",
            str(audio),
        ]
        self.runner(command, check=True, capture_output=True, text=True)

    def cleanup(self, work_dir: Path) -> None:
        resolved = self._require_work_dir(work_dir)
        if work_dir.is_symlink():
            raise ValueError("work directory symlink is not allowed within temporary root")
        if resolved.exists():
            shutil.rmtree(resolved)

    def _require_work_dir(self, work_dir: Path) -> Path:
        resolved = work_dir.resolve()
        if resolved == self.root or not resolved.is_relative_to(self.root):
            raise ValueError("path is outside dedicated temporary root")
        return resolved
