"""Local speech transcription with a conservative fallback policy."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Protocol

from .models import TranscriptSegment


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    segments: tuple[TranscriptSegment, ...]
    engine: str
    model: str
    language: str | None


class TranscriptionEngine(Protocol):
    def transcribe(self, audio_path: Path) -> TranscriptResult: ...


def is_usable(result: TranscriptResult, voiced_seconds: float) -> bool:
    """Return whether a primary transcript is credible enough to retain."""
    if not math.isfinite(voiced_seconds) or voiced_seconds < 0:
        raise ValueError("voiced_seconds must be finite and non-negative")
    compact = re.sub(r"\s+", "", result.text)
    if not compact:
        return False
    if voiced_seconds >= 5 and len(compact) < 4:
        return False
    if result.text.count("[UNKNOWN]") >= 2:
        return False
    most_repeated = max((compact.count(character) for character in set(compact)), default=0)
    return most_repeated / len(compact) < 0.8


class UnusableTranscriptError(ValueError):
    """Both transcription engines produced text rejected by the quality gate."""


class FallbackTranscriber:
    def __init__(self, primary: TranscriptionEngine, fallback: TranscriptionEngine) -> None:
        self.primary = primary
        self.fallback = fallback

    def transcribe(self, audio_path: Path, voiced_seconds: float) -> TranscriptResult:
        primary_result = self.primary.transcribe(audio_path)
        if is_usable(primary_result, voiced_seconds):
            return primary_result
        fallback_result = self.fallback.transcribe(audio_path)
        if not is_usable(fallback_result, voiced_seconds):
            raise UnusableTranscriptError("unusable_transcript")
        return fallback_result


DurationProbe = Callable[[Path], float]
ChunkWriter = Callable[[Path, Path, float, float], None]


def _probe_duration(audio_path: Path) -> float:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        duration = float(completed.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as exc:
        raise RuntimeError("media_probe_failed") from exc
    if not math.isfinite(duration) or duration < 0:
        raise RuntimeError("media_probe_failed")
    return duration


def _write_chunk(source: Path, destination: Path, start: float, duration: float) -> None:
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(start),
                "-t",
                str(duration),
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-y",
                str(destination),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("audio_chunk_failed") from exc


class ResumableChunkedEngine:
    """Transcribe bounded audio chunks and durably checkpoint each result."""

    def __init__(
        self,
        engine: TranscriptionEngine,
        *,
        chunk_seconds: float = 30.0,
        duration_probe: DurationProbe = _probe_duration,
        chunk_writer: ChunkWriter = _write_chunk,
    ) -> None:
        if not math.isfinite(chunk_seconds) or chunk_seconds <= 0:
            raise ValueError("chunk_seconds must be finite and positive")
        self.engine = engine
        self.chunk_seconds = float(chunk_seconds)
        self.duration_probe = duration_probe
        self.chunk_writer = chunk_writer

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        audio_path = Path(audio_path)
        duration = float(self.duration_probe(audio_path))
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("audio duration must be finite and non-negative")
        if duration <= self.chunk_seconds:
            return self.engine.transcribe(audio_path)

        identity = self._identity(audio_path, duration)
        checkpoint_path, chunk_prefix = self._paths(audio_path)
        completed = self._load_checkpoint(checkpoint_path, identity)
        chunk_count = math.ceil(duration / self.chunk_seconds)
        for index in range(len(completed), chunk_count):
            start = index * self.chunk_seconds
            chunk_duration = min(self.chunk_seconds, duration - start)
            chunk_path = audio_path.with_name(f"{chunk_prefix}.chunk-{index:05d}.wav")
            try:
                self.chunk_writer(audio_path, chunk_path, start, chunk_duration)
                completed.append(self.engine.transcribe(chunk_path))
                self._save_checkpoint(checkpoint_path, identity, completed)
            finally:
                chunk_path.unlink(missing_ok=True)
        return self._combine(completed)

    def _identity(self, audio_path: Path, duration: float) -> dict[str, Any]:
        stat = audio_path.stat()
        return {
            "audio_size": stat.st_size,
            "audio_mtime_ns": stat.st_mtime_ns,
            "duration_seconds": duration,
            "chunk_seconds": self.chunk_seconds,
            "engine": type(self.engine).__qualname__,
            "model": str(getattr(self.engine, "model_name", "unknown")),
        }

    def _paths(self, audio_path: Path) -> tuple[Path, str]:
        engine_key = (
            f"{type(self.engine).__qualname__}:"
            f"{getattr(self.engine, 'model_name', 'unknown')}"
        )
        digest = hashlib.sha256(engine_key.encode("utf-8")).hexdigest()[:12]
        prefix = f".{audio_path.stem}.{digest}"
        return audio_path.with_name(f"{prefix}.chunks.json"), prefix

    @staticmethod
    def _load_checkpoint(
        checkpoint_path: Path, identity: dict[str, Any]
    ) -> list[TranscriptResult]:
        if not checkpoint_path.exists():
            return []
        try:
            payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if payload.get("version") != 1 or payload.get("identity") != identity:
                return []
            results = payload["completed"]
            if not isinstance(results, list):
                raise ValueError
            return [ResumableChunkedEngine._result_from_dict(value) for value in results]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_chunk_checkpoint") from exc

    @staticmethod
    def _save_checkpoint(
        checkpoint_path: Path,
        identity: dict[str, Any],
        completed: list[TranscriptResult],
    ) -> None:
        payload = {
            "version": 1,
            "identity": identity,
            "completed": [ResumableChunkedEngine._result_to_dict(item) for item in completed],
        }
        temporary = checkpoint_path.with_suffix(checkpoint_path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(checkpoint_path)

    @staticmethod
    def _result_to_dict(result: TranscriptResult) -> dict[str, Any]:
        return {
            "text": result.text,
            "segments": [segment.to_dict() for segment in result.segments],
            "engine": result.engine,
            "model": result.model,
            "language": result.language,
        }

    @staticmethod
    def _result_from_dict(value: Any) -> TranscriptResult:
        if not isinstance(value, dict):
            raise ValueError
        return TranscriptResult(
            text=str(value["text"]),
            segments=tuple(
                TranscriptSegment.from_dict(segment) for segment in value["segments"]
            ),
            engine=str(value["engine"]),
            model=str(value["model"]),
            language=None if value.get("language") is None else str(value["language"]),
        )

    def _combine(self, results: list[TranscriptResult]) -> TranscriptResult:
        if not results:
            raise ValueError("empty_chunk_transcript")
        first = results[0]
        segments = tuple(
            TranscriptSegment(
                segment.start + index * self.chunk_seconds,
                segment.end + index * self.chunk_seconds,
                segment.text,
            )
            for index, result in enumerate(results)
            for segment in result.segments
        )
        languages = {result.language for result in results if result.language is not None}
        return TranscriptResult(
            text=" ".join(result.text for result in results if result.text).strip(),
            segments=segments,
            engine=first.engine,
            model=first.model,
            language=languages.pop() if len(languages) == 1 else None,
        )


class SenseVoiceEngine:
    def __init__(
        self,
        model: str = "iic/SenseVoiceSmall",
        *,
        device: str = "cpu",
        model_factory: Callable[..., Any] | None = None,
        postprocessor: Callable[[str], str] | None = None,
    ) -> None:
        self.model_name = model
        self.device = device
        self._model_factory = model_factory
        self._model: Any | None = None
        self._postprocessor = postprocessor

    def _load_model(self) -> Any:
        if self._model is None:
            factory = self._model_factory
            if factory is None:
                from funasr import AutoModel

                factory = AutoModel
            self._model = factory(model=self.model_name, device=self.device)
        return self._model

    def _load_postprocessor(self) -> Callable[[str], str]:
        if self._postprocessor is None:
            from funasr.utils.postprocess_utils import rich_transcription_postprocess

            self._postprocessor = rich_transcription_postprocess
        return self._postprocessor

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        generated = self._load_model().generate(input=str(audio_path))
        entry = generated[0] if generated else {}
        postprocess = self._load_postprocessor()
        text = postprocess(str(entry.get("text", ""))).strip()
        segments = tuple(
            TranscriptSegment(
                start=float(segment["start"]) / 1000.0,
                end=float(segment["end"]) / 1000.0,
                text=postprocess(str(segment.get("text", ""))).strip(),
            )
            for segment in entry.get("sentence_info", ())
        )
        language = entry.get("language")
        return TranscriptResult(
            text=text,
            segments=segments,
            engine="sensevoice",
            model=self.model_name,
            language=None if language is None else str(language),
        )


class FasterWhisperEngine:
    def __init__(
        self,
        model: str = "small",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self._model_factory = model_factory
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is None:
            factory = self._model_factory
            if factory is None:
                from faster_whisper import WhisperModel

                factory = WhisperModel
            self._model = factory(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        generated, info = self._load_model().transcribe(str(audio_path))
        segments = tuple(
            TranscriptSegment(
                start=float(segment.start),
                end=float(segment.end),
                text=str(segment.text).strip(),
            )
            for segment in generated
        )
        return TranscriptResult(
            text=" ".join(segment.text for segment in segments).strip(),
            segments=segments,
            engine="faster-whisper",
            model=self.model_name,
            language=None if getattr(info, "language", None) is None else str(info.language),
        )
