from pathlib import Path

import pytest

from pkb.douyin.models import TranscriptSegment
from pkb.douyin.transcription import (
    FallbackTranscriber,
    FasterWhisperEngine,
    ResumableChunkedEngine,
    SenseVoiceEngine,
    TranscriptResult,
    UnusableTranscriptError,
    is_usable,
)


class ChunkEngine:
    model_name = "chunk-model"

    def __init__(self, *, fail_once_at: int | None = None) -> None:
        self.calls: list[int] = []
        self.fail_once_at = fail_once_at

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        chunk_index = int(audio_path.stem.rsplit("-", 1)[1])
        self.calls.append(chunk_index)
        if self.fail_once_at == chunk_index:
            self.fail_once_at = None
            raise RuntimeError("interrupted")
        return TranscriptResult(
            text=f"part {chunk_index}",
            segments=(TranscriptSegment(1.0, 2.0, f"part {chunk_index}"),),
            engine="sensevoice",
            model=self.model_name,
            language="zh",
        )


def write_fake_chunk(source: Path, destination: Path, start: float, duration: float) -> None:
    assert source.name == "audio.wav"
    destination.write_text(f"{start}:{duration}", encoding="utf-8")


def test_chunked_engine_combines_results_with_absolute_timestamps(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    inner = ChunkEngine()
    engine = ResumableChunkedEngine(
        inner,
        chunk_seconds=30,
        duration_probe=lambda _path: 65,
        chunk_writer=write_fake_chunk,
    )

    got = engine.transcribe(audio)

    assert inner.calls == [0, 1, 2]
    assert got.text == "part 0 part 1 part 2"
    assert got.segments == (
        TranscriptSegment(1.0, 2.0, "part 0"),
        TranscriptSegment(31.0, 32.0, "part 1"),
        TranscriptSegment(61.0, 62.0, "part 2"),
    )


def test_chunked_engine_resumes_after_last_durable_chunk(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    first = ChunkEngine(fail_once_at=1)
    first_run = ResumableChunkedEngine(
        first,
        chunk_seconds=30,
        duration_probe=lambda _path: 65,
        chunk_writer=write_fake_chunk,
    )

    with pytest.raises(RuntimeError, match="interrupted"):
        first_run.transcribe(audio)

    resumed = ChunkEngine()
    got = ResumableChunkedEngine(
        resumed,
        chunk_seconds=30,
        duration_probe=lambda _path: 65,
        chunk_writer=write_fake_chunk,
    ).transcribe(audio)

    assert first.calls == [0, 1]
    assert resumed.calls == [1, 2]
    assert got.text == "part 0 part 1 part 2"


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


def test_bad_fallback_is_rejected_instead_of_persisting_empty_text():
    transcriber = FallbackTranscriber(FakeEngine(result("")), FakeEngine(result("")))

    with pytest.raises(UnusableTranscriptError, match="unusable_transcript"):
        transcriber.transcribe(Path("a.wav"), voiced_seconds=8)


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

    engine = SenseVoiceEngine(model_factory=factory, postprocessor=lambda text: text)
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


def test_sensevoice_postprocesses_tagged_transcript_and_segment_text():
    tagged = "<|zh|><|NEUTRAL|><|Speech|>hello"

    class Model:
        def generate(self, **kwargs):
            return [{
                "text": tagged,
                "sentence_info": [{"start": 0, "end": 1000, "text": tagged}],
            }]

    calls = []

    def postprocess(text):
        calls.append(text)
        return "hello"

    engine = SenseVoiceEngine(model_factory=lambda **_: Model(), postprocessor=postprocess)
    got = engine.transcribe(Path("speech.wav"))

    assert got.text == "hello"
    assert got.segments == (TranscriptSegment(0.0, 1.0, "hello"),)
    assert calls == [tagged, tagged]


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
