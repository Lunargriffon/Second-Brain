"""Local speech transcription with a conservative fallback policy."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
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


class FallbackTranscriber:
    def __init__(self, primary: TranscriptionEngine, fallback: TranscriptionEngine) -> None:
        self.primary = primary
        self.fallback = fallback

    def transcribe(self, audio_path: Path, voiced_seconds: float) -> TranscriptResult:
        primary_result = self.primary.transcribe(audio_path)
        if is_usable(primary_result, voiced_seconds):
            return primary_result
        return self.fallback.transcribe(audio_path)


class SenseVoiceEngine:
    def __init__(
        self,
        model: str = "iic/SenseVoiceSmall",
        *,
        device: str = "cpu",
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = model
        self.device = device
        self._model_factory = model_factory
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is None:
            factory = self._model_factory
            if factory is None:
                from funasr import AutoModel

                factory = AutoModel
            self._model = factory(model=self.model_name, device=self.device)
        return self._model

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        generated = self._load_model().generate(input=str(audio_path))
        entry = generated[0] if generated else {}
        text = str(entry.get("text", "")).strip()
        segments = tuple(
            TranscriptSegment(
                start=float(segment["start"]) / 1000.0,
                end=float(segment["end"]) / 1000.0,
                text=str(segment.get("text", "")).strip(),
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
