from datetime import datetime, timezone
import json

import pytest

from pkb.douyin.eligibility import Eligibility, EligibilityDecision
from pkb.douyin.manifest import ManifestStore
from pkb.douyin.models import FavoriteItem, Stage
from pkb.douyin.rebuild import invalidate_empty_kept_records, rebuild_filtered_corpus


NOW = datetime(2026, 9, 10, 8, 9, 10, tzinfo=timezone.utc)


def item(work_id: str) -> FavoriteItem:
    return FavoriteItem(
        work_id=work_id,
        url=f"https://www.douyin.com/video/{work_id}",
        author_id="author",
        author="author",
        caption="caption",
        hashtags=(),
        published_at=None,
        observed_at="2026-09-10T00:00:00Z",
    )


def classify(store: ManifestStore, work_id: str, eligibility: Eligibility) -> None:
    store.set_eligibility(
        work_id,
        EligibilityDecision(
            eligibility=eligibility,
            reasons=("test",),
            classifier_version="rules-v1",
            input_hash=f"hash-{work_id}",
        ),
    )


def line(work_id: str, transcript: str = "knowledge") -> str:
    return json.dumps(
        {"work_id": work_id, "transcript_text": transcript},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_rebuild_keeps_only_currently_eligible_records_and_creates_backup(tmp_path):
    raw = tmp_path / "douyin-favorites.jsonl"
    original_keep = line("keep")
    raw.write_text(original_keep + "\n" + line("exclude") + "\n", encoding="utf-8")
    manifest = ManifestStore(tmp_path / "state.json")
    manifest.discover([item("keep"), item("exclude")])
    classify(manifest, "keep", Eligibility.KEEP)
    classify(manifest, "exclude", Eligibility.EXCLUDE)

    report = rebuild_filtered_corpus(raw, manifest, tmp_path / "backups", now=NOW)

    assert raw.read_text(encoding="utf-8") == original_keep + "\n"
    assert report.kept == 1
    assert report.removed == 1
    assert report.changed is True
    assert report.backup_path is not None
    assert report.backup_path.name == "douyin-favorites.20260910T080910Z.bak.jsonl"
    assert "exclude" in report.backup_path.read_text(encoding="utf-8")


def test_rebuild_is_noop_without_excluded_raw_records(tmp_path):
    raw = tmp_path / "douyin-favorites.jsonl"
    raw.write_text(line("keep") + "\n", encoding="utf-8")
    manifest = ManifestStore(tmp_path / "state.json")
    manifest.discover([item("keep")])
    classify(manifest, "keep", Eligibility.KEEP)

    report = rebuild_filtered_corpus(raw, manifest, tmp_path / "backups", now=NOW)

    assert report.changed is False
    assert report.backup_path is None
    assert not (tmp_path / "backups").exists()


def test_rebuild_rejects_unclassified_manifest_without_touching_raw(tmp_path):
    raw = tmp_path / "douyin-favorites.jsonl"
    original = line("one") + "\n"
    raw.write_text(original, encoding="utf-8")
    manifest = ManifestStore(tmp_path / "state.json")
    manifest.discover([item("one")])

    with pytest.raises(ValueError, match="unclassified"):
        rebuild_filtered_corpus(raw, manifest, tmp_path / "backups", now=NOW)

    assert raw.read_text(encoding="utf-8") == original
    assert not (tmp_path / "backups").exists()


def test_rebuild_rejects_duplicate_or_empty_transcript_before_replacement(tmp_path):
    raw = tmp_path / "douyin-favorites.jsonl"
    original = line("one") + "\n" + line("one", "") + "\n"
    raw.write_text(original, encoding="utf-8")
    manifest = ManifestStore(tmp_path / "state.json")
    manifest.discover([item("one")])
    classify(manifest, "one", Eligibility.KEEP)

    with pytest.raises(ValueError, match="duplicate work ID"):
        rebuild_filtered_corpus(raw, manifest, tmp_path / "backups", now=NOW)

    assert raw.read_text(encoding="utf-8") == original
    assert not (tmp_path / "backups").exists()


def test_rebuild_rejects_empty_kept_transcript_before_replacement(tmp_path):
    raw = tmp_path / "douyin-favorites.jsonl"
    original = line("one", "") + "\n"
    raw.write_text(original, encoding="utf-8")
    manifest = ManifestStore(tmp_path / "state.json")
    manifest.discover([item("one")])
    classify(manifest, "one", Eligibility.KEEP)

    with pytest.raises(ValueError, match="empty transcript"):
        rebuild_filtered_corpus(raw, manifest, tmp_path / "backups", now=NOW)

    assert raw.read_text(encoding="utf-8") == original
    assert not (tmp_path / "backups").exists()


def test_rebuild_removes_empty_excluded_record_with_the_other_exclusions(tmp_path):
    raw = tmp_path / "douyin-favorites.jsonl"
    original_keep = line("keep")
    raw.write_text(original_keep + "\n" + line("exclude", "") + "\n", encoding="utf-8")
    manifest = ManifestStore(tmp_path / "state.json")
    manifest.discover([item("keep"), item("exclude")])
    classify(manifest, "keep", Eligibility.KEEP)
    classify(manifest, "exclude", Eligibility.EXCLUDE)

    report = rebuild_filtered_corpus(raw, manifest, tmp_path / "backups", now=NOW)

    assert report.removed == 1
    assert raw.read_text(encoding="utf-8") == original_keep + "\n"


def test_empty_kept_record_is_backed_up_removed_and_reset_for_retry(tmp_path):
    raw = tmp_path / "douyin-favorites.jsonl"
    original = line("good") + "\n" + line("bad", "") + "\n"
    raw.write_text(original, encoding="utf-8")
    manifest = ManifestStore(tmp_path / "state.json")
    manifest.discover([item("good"), item("bad")])
    classify(manifest, "good", Eligibility.KEEP)
    classify(manifest, "bad", Eligibility.KEEP)
    for work_id in ("good", "bad"):
        manifest.update(work_id, Stage.ACQUIRED)
        manifest.update(work_id, Stage.AUDIO_READY)
        manifest.update(work_id, Stage.TRANSCRIBED)
        manifest.update(work_id, Stage.PERSISTED)
        manifest.update(work_id, Stage.CLEANED)

    count = invalidate_empty_kept_records(
        raw, manifest, tmp_path / "backups", now=NOW
    )

    assert count == 1
    assert raw.read_text(encoding="utf-8") == line("good") + "\n"
    assert manifest.get("bad").stage.value == "failed"
    backups = list((tmp_path / "backups").glob("*.invalid.bak.jsonl"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
