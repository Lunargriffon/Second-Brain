import json
from pathlib import Path

import pytest

from pkb.douyin.manifest import ManifestStore
from pkb.douyin.eligibility import Eligibility, EligibilityDecision
from pkb.douyin.models import FavoriteItem, Stage


def item(work_id: str, *, stage: Stage = Stage.DISCOVERED) -> FavoriteItem:
    return FavoriteItem(
        work_id=work_id,
        url=f"https://www.douyin.com/video/{work_id}",
        author_id="u1",
        author="author",
        caption="content",
        hashtags=("knowledge",),
        published_at=None,
        observed_at="2026-07-18T00:00:00Z",
        stage=stage,
    )


def test_manifest_round_trip_and_idempotent_discovery(tmp_path):
    store = ManifestStore(tmp_path / "state.json")
    store.discover([item("1"), item("1"), item("2")])

    assert [entry.work_id for entry in ManifestStore(store.path).items()] == ["1", "2"]
    assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == 2


def test_manifest_reads_version_one_and_migrates_on_next_write(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": 1, "items": [item("1").to_dict()]}),
        encoding="utf-8",
    )
    store = ManifestStore(path)

    assert store.get("1").eligibility is None
    store.discover([item("2")])

    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_manifest_atomic_write_preserves_old_file_on_replace_failure(tmp_path, monkeypatch):
    store = ManifestStore(tmp_path / "state.json")
    store.discover([item("1")])
    monkeypatch.setattr(Path, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk")))

    with pytest.raises(OSError, match="disk"):
        store.update("1", Stage.ACQUIRED)

    assert ManifestStore(store.path).get("1").stage is Stage.DISCOVERED


def test_discovery_keeps_existing_state_and_appends_new_items(tmp_path):
    store = ManifestStore(tmp_path / "state.json")
    store.discover([item("1")])
    store.update("1", Stage.ACQUIRED)

    store.discover([item("1"), item("2")])

    assert store.get("1").stage is Stage.ACQUIRED
    assert [entry.work_id for entry in store.items()] == ["1", "2"]


def test_update_checks_work_id_and_stage_transition(tmp_path):
    store = ManifestStore(tmp_path / "state.json")
    store.discover([item("1")])

    with pytest.raises(KeyError, match="missing"):
        store.update("missing", Stage.ACQUIRED)
    with pytest.raises(ValueError, match="discovered -> indexed"):
        store.update("1", Stage.INDEXED)

    assert store.get("1").stage is Stage.DISCOVERED


def test_pending_excludes_terminal_and_persisted_items(tmp_path):
    store = ManifestStore(tmp_path / "state.json")
    store.discover(
        [
            item("1"),
            item("2", stage=Stage.FAILED),
            item("3", stage=Stage.PERSISTED),
            item("4", stage=Stage.CLEANED),
            item("5", stage=Stage.UNAVAILABLE),
        ]
    )

    assert [entry.work_id for entry in store.pending()] == ["1", "2", "3"]


def test_excluded_item_is_not_pending_but_kept_item_is_pending(tmp_path):
    store = ManifestStore(tmp_path / "state.json")
    store.discover([item("keep"), item("exclude")])
    store.set_eligibility("keep", decision(Eligibility.KEEP, "keep-hash"))
    store.set_eligibility("exclude", decision(Eligibility.EXCLUDE, "exclude-hash"))

    assert [entry.work_id for entry in store.pending()] == ["keep"]


def test_classification_is_stale_when_version_or_input_changes(tmp_path):
    store = ManifestStore(tmp_path / "state.json")
    store.discover([item("1")])
    stored = decision(Eligibility.KEEP, "hash-1")
    store.set_eligibility("1", stored)

    assert store.needs_classification("1", "rules-v1", "hash-1") is False
    assert store.needs_classification("1", "rules-v2", "hash-1") is True
    assert store.needs_classification("1", "rules-v1", "hash-2") is True


def test_discovery_refreshes_metadata_without_resetting_media_stage(tmp_path):
    store = ManifestStore(tmp_path / "state.json")
    store.discover([item("1")])
    store.update("1", Stage.ACQUIRED)
    refreshed = item("1")
    refreshed = FavoriteItem.from_dict({**refreshed.to_dict(), "caption": "new caption"})

    store.discover([refreshed])

    assert store.get("1").caption == "new caption"
    assert store.get("1").stage is Stage.ACQUIRED


def test_invalid_cleaned_item_can_be_reset_for_reprocessing(tmp_path):
    store = ManifestStore(tmp_path / "state.json")
    store.discover([item("1", stage=Stage.CLEANED)])

    reset = store.reset_for_reprocessing("1")

    assert reset.stage is Stage.FAILED
    assert store.get("1").stage is Stage.FAILED


def decision(eligibility: Eligibility, input_hash: str) -> EligibilityDecision:
    return EligibilityDecision(
        eligibility=eligibility,
        reasons=("test_reason",),
        classifier_version="rules-v1",
        input_hash=input_hash,
    )
