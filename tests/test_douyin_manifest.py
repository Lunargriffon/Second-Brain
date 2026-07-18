from pathlib import Path

import pytest

from pkb.douyin.manifest import ManifestStore
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
