import json

from pkb.checkpoint import CheckpointStore


def test_checkpoint_loads_zero_state_when_file_does_not_exist(tmp_path):
    store = CheckpointStore(tmp_path / "data" / "state" / "zhihu.state.json")

    checkpoint = store.load()

    assert checkpoint.offset == 0
    assert checkpoint.exported == 0


def test_checkpoint_saves_state_in_independent_json_file(tmp_path):
    state_path = tmp_path / "data" / "state" / "zhihu.state.json"
    store = CheckpointStore(state_path)

    store.save(offset=20, exported=18)

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["offset"] == 20
    assert payload["exported"] == 18
    assert "updated_at" in payload


def test_checkpoint_loads_existing_state(tmp_path):
    state_path = tmp_path / "data" / "state" / "zhihu.state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"offset": 40, "exported": 39, "updated_at": "2026-06-30T12:00:00Z"}', encoding="utf-8")

    checkpoint = CheckpointStore(state_path).load()

    assert checkpoint.offset == 40
    assert checkpoint.exported == 39
