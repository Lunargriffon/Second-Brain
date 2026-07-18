import json
from pathlib import Path

import pytest

from pkb.douyin.manifest import ManifestStore
from pkb.douyin.media import AcquisitionFailure, MediaPaths
from pkb.douyin.models import FavoriteItem, Stage, TranscriptSegment
from pkb.douyin.pipeline import DouyinPipeline, DurableJsonlStore, MediaInfo
from pkb.douyin.transcription import TranscriptResult


def item(work_id="one"):
    return FavoriteItem(work_id, f"https://douyin.example/{work_id}", "author-id", "作者", "标题", ("知识",), None, "2026-07-18T00:00:00Z")


class FakeMedia:
    def __init__(self, root: Path):
        self.root = root
        self.cleaned = []

    def prepare(self, work_id):
        directory = self.root / work_id
        directory.mkdir(parents=True, exist_ok=True)
        return MediaPaths(directory, directory / "video.mp4", directory / "audio.wav")

    def extract_audio(self, paths):
        paths.audio.write_bytes(b"audio")

    def cleanup(self, work_dir):
        self.cleaned.append(work_dir.name)


class FakeAcquirer:
    def __init__(self, failures=None):
        self.failures = failures or {}
        self.calls = []

    def acquire(self, favorite, destination):
        self.calls.append(favorite.work_id)
        if favorite.work_id in self.failures:
            raise AcquisitionFailure(self.failures[favorite.work_id])
        destination.write_bytes(b"video")


class FakeTranscriber:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio_path, voiced_seconds):
        self.calls.append((audio_path.parent.name, voiced_seconds))
        return TranscriptResult("你好世界", (TranscriptSegment(0, 1, "你好世界"),), "fake", "tiny", "zh")


def build(tmp_path, favorites, failures=None):
    manifest = ManifestStore(tmp_path / "manifest.json")
    manifest.discover(favorites)
    media = FakeMedia(tmp_path / "tmp")
    acquirer = FakeAcquirer(failures)
    transcriber = FakeTranscriber()
    pipeline = DouyinPipeline(
        manifest=manifest,
        raw_store=DurableJsonlStore(tmp_path / "raw.jsonl"),
        media=media,
        acquirer=acquirer,
        transcriber=transcriber,
        probe=lambda _paths: MediaInfo(duration_seconds=12.0, voiced_seconds=9.0),
    )
    return pipeline, manifest, media, acquirer, transcriber


def test_pipeline_persists_before_cleanup_and_reaches_cleaned(tmp_path, monkeypatch):
    pipeline, manifest, media, _, transcriber = build(tmp_path, [item()])
    syncs = []
    monkeypatch.setattr("pkb.douyin.pipeline.os.fsync", lambda fd: syncs.append(fd))
    original_cleanup = media.cleanup
    def cleanup_after_sync(work_dir):
        assert syncs, "raw record must be fsynced before temporary media cleanup"
        original_cleanup(work_dir)
    media.cleanup = cleanup_after_sync

    audit = pipeline.run()

    assert manifest.get("one").stage is Stage.CLEANED
    assert media.cleaned == ["one"]
    assert syncs
    record = json.loads((tmp_path / "raw.jsonl").read_text(encoding="utf-8"))
    assert record["work_id"] == "one"
    assert record["transcript_text"] == "你好世界"
    assert record["source_duration_seconds"] == 12
    assert transcriber.calls == [("one", 9.0)]
    assert audit.counts == {"persisted": 1, "cleaned": 1}


def test_durable_store_is_append_once_by_work_id(tmp_path):
    store = DurableJsonlStore(tmp_path / "raw.jsonl")
    payload = {"work_id": "same", "value": 1}
    assert store.append_once(payload) is True
    assert store.append_once({"work_id": "same", "value": 2}) is False
    assert (tmp_path / "raw.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_stop_run_records_safe_code_and_does_not_start_later_items(tmp_path):
    pipeline, manifest, _, acquirer, _ = build(
        tmp_path, [item("blocked"), item("later")], {"blocked": "captcha"}
    )

    audit = pipeline.run()

    assert audit.stopped is True
    assert audit.errors == {"captcha": 1}
    assert acquirer.calls == ["blocked"]
    assert manifest.get("blocked").stage is Stage.FAILED
    assert manifest.get("later").stage is Stage.DISCOVERED


def test_unavailable_is_terminal_but_next_item_continues(tmp_path):
    pipeline, manifest, _, acquirer, _ = build(
        tmp_path, [item("gone"), item("ok")], {"gone": "http_404"}
    )
    audit = pipeline.run()
    assert manifest.get("gone").stage is Stage.UNAVAILABLE
    assert manifest.get("ok").stage is Stage.CLEANED
    assert acquirer.calls == ["gone", "ok"]
    assert audit.errors == {"http_404": 1}


def test_persisted_item_resumes_cleanup_without_duplicate_or_retranscription(tmp_path):
    pipeline, manifest, media, _, transcriber = build(tmp_path, [item()])
    manifest.update("one", Stage.ACQUIRED)
    manifest.update("one", Stage.AUDIO_READY)
    manifest.update("one", Stage.TRANSCRIBED)
    manifest.update("one", Stage.PERSISTED)
    store = DurableJsonlStore(tmp_path / "raw.jsonl")
    store.append_once({"work_id": "one"})

    audit = pipeline.run()

    assert manifest.get("one").stage is Stage.CLEANED
    assert transcriber.calls == []
    assert media.cleaned == ["one"]
    assert audit.counts == {"cleaned": 1}
    assert (tmp_path / "raw.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_cleanup_failure_leaves_persisted_for_retry(tmp_path):
    pipeline, manifest, media, _, _ = build(tmp_path, [item()])
    media.cleanup = lambda _path: (_ for _ in ()).throw(OSError("private detail"))
    audit = pipeline.run()
    assert manifest.get("one").stage is Stage.PERSISTED
    assert audit.errors == {"cleanup_failed": 1}
    assert audit.cleanup_pending == 1


def test_indexed_item_can_resume_cleanup(tmp_path):
    pipeline, manifest, media, _, transcriber = build(tmp_path, [item()])
    for stage in (Stage.ACQUIRED, Stage.AUDIO_READY, Stage.TRANSCRIBED, Stage.PERSISTED, Stage.INDEXED):
        manifest.update("one", stage)

    audit = pipeline.run()

    assert manifest.get("one").stage is Stage.CLEANED
    assert media.cleaned == ["one"]
    assert transcriber.calls == []
    assert audit.counts == {"cleaned": 1}


def test_second_run_is_idempotent(tmp_path):
    pipeline, manifest, media, acquirer, transcriber = build(tmp_path, [item()])
    pipeline.run()
    first_raw = (tmp_path / "raw.jsonl").read_bytes()

    audit = pipeline.run()

    assert manifest.get("one").stage is Stage.CLEANED
    assert (tmp_path / "raw.jsonl").read_bytes() == first_raw
    assert acquirer.calls == ["one"]
    assert len(transcriber.calls) == 1
    assert media.cleaned == ["one"]
    assert audit.counts == {}
