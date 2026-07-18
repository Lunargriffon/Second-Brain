from pathlib import Path

import pytest

from pkb.douyin.models import TranscriptSegment
from pkb.douyin.transcription import (
    FallbackTranscriber,
    FasterWhisperEngine,
    SenseVoiceEngine,
    TranscriptResult,
    is_usable,
)


def result(text: str, *, engine: str = "sensevoice") -> TranscriptResult:
    segments = () if not text else (TranscriptSegment(0.0, 4.0, text),)
    return TranscriptResult(text, segments, engine, "test-model", "zh")


class FakeEngine:
    def __init__(self, outcome: TranscriptResult | None = None) -> None:
        self.outcome = outcome
        self.calls = 0

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        self.calls += 1
        assert self.outcome is not None
        return self.outcome


def test_primary_valid_transcript_does_not_call_fallback():
    primary = FakeEngine(result("complete knowledge content"))
    fallback = FakeEngine()
    got = FallbackTranscriber(primary, fallback).transcribe(Path("a.wav"), voiced_seconds=8)
    assert got.engine == "sensevoice"
    assert fallback.calls == 0


@pytest.mark.parametrize("text", ["", "aaaaaaaa", "[UNKNOWN] [UNKNOWN]"])
def test_bad_primary_uses_faster_whisper(text):
    fallback = FakeEngine(result("useful content", engine="faster-whisper"))
    got = FallbackTranscriber(FakeEngine(result(text)), fallback).transcribe(
        Path("a.wav"), voiced_seconds=8
    )
    assert got.engine == "faster-whisper"
    assert fallback.calls == 1


def test_quality_gate_rejects_short_speech_but_allows_short_clip():
    terse = result("ok")
    assert not is_usable(terse, voiced_seconds=5)
    assert is_usable(terse, voiced_seconds=1)


def test_sensevoice_is_lazy_and_preserves_metadata_and_segments():
    created = []

    class Model:
        def generate(self, **kwargs):
            assert kwargs["input"] == "speech.wav"
            return [{
                "text": "First. Second.",
                "language": "zh",
                "sentence_info": [
                    {"start": 0, "end": 1250, "text": "First."},
                    {"start": 1250, "end": 2500, "text": "Second."},
                ],
            }]

    def factory(**kwargs):
        created.append(kwargs)
        return Model()

    engine = SenseVoiceEngine(model_factory=factory)
    assert created == []
    got = engine.transcribe(Path("speech.wav"))
    assert created == [{"model": "iic/SenseVoiceSmall", "device": "cpu"}]
    assert got.engine == "sensevoice"
    assert got.model == "iic/SenseVoiceSmall"
    assert got.language == "zh"
    assert got.segments == (
        TranscriptSegment(0.0, 1.25, "First."),
        TranscriptSegment(1.25, 2.5, "Second."),
    )


def test_faster_whisper_is_lazy_and_defaults_to_cpu_int8():
    created = []

    class Segment:
        start = 0.25
        end = 1.5
        text = " fallback text "

    class Info:
        language = "en"

    class Model:
        def transcribe(self, audio_path):
            assert audio_path == "speech.wav"
            return iter([Segment()]), Info()

    def factory(model, **kwargs):
        created.append((model, kwargs))
        return Model()

    engine = FasterWhisperEngine(model_factory=factory)
    assert created == []
    got = engine.transcribe(Path("speech.wav"))
    assert created == [("small", {"device": "cpu", "compute_type": "int8"})]
    assert got == TranscriptResult(
        "fallback text",
        (TranscriptSegment(0.25, 1.5, "fallback text"),),
        "faster-whisper",
        "small",
        "en",
    )
