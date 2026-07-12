import json
from pathlib import Path

from pkb.checkpoint import CheckpointStore
from pkb.exporter import ExportResult, export_zhihu_collection
from pkb.jsonl import JsonlWriter
from pkb.zhihu import FakeZhihuClient


def test_exporter_writes_articles_and_updates_checkpoint(tmp_path):
    output = tmp_path / "data" / "raw" / "zhihu.jsonl"
    state_path = tmp_path / "data" / "state" / "zhihu.state.json"
    client = FakeZhihuClient(Path("tests/fixtures/sample_collection.json"))

    result = export_zhihu_collection(
        collection_url="https://www.zhihu.com/collection/1",
        client=client,
        writer=JsonlWriter(output),
        checkpoint_store=CheckpointStore(state_path),
    )

    lines = output.read_text(encoding="utf-8").splitlines()
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert result == ExportResult(exported=2, skipped=0)
    assert len(lines) == 2
    assert state["offset"] == 2
    assert state["exported"] == 2


def test_exporter_resumes_from_checkpoint(tmp_path):
    output = tmp_path / "data" / "raw" / "zhihu.jsonl"
    state_path = tmp_path / "data" / "state" / "zhihu.state.json"
    CheckpointStore(state_path).save(offset=1, exported=1)
    client = FakeZhihuClient(Path("tests/fixtures/sample_collection.json"))

    result = export_zhihu_collection(
        collection_url="https://www.zhihu.com/collection/1",
        client=client,
        writer=JsonlWriter(output),
        checkpoint_store=CheckpointStore(state_path),
    )

    lines = output.read_text(encoding="utf-8").splitlines()
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert result == ExportResult(exported=1, skipped=0)
    assert json.loads(lines[0])["id"] == "zhihu_789012"
    assert state["offset"] == 2
    assert state["exported"] == 2


def test_exporter_skips_articles_already_present_in_output(tmp_path):
    output = tmp_path / "data" / "raw" / "zhihu.jsonl"
    state_path = tmp_path / "data" / "state" / "zhihu.state.json"
    writer = JsonlWriter(output)
    writer.write(
        {
            "schema_version": "1.0",
            "id": "zhihu_123456",
            "source": "zhihu",
            "title": "Existing",
            "url": "https://example.test/existing",
            "author": "Author",
            "content": "Content",
            "created_at": "2024-01-15T04:00:00Z",
            "saved_at": "2024-01-15T04:00:00Z",
            "images": [],
        }
    )
    client = FakeZhihuClient(Path("tests/fixtures/sample_collection.json"))

    result = export_zhihu_collection(
        collection_url="https://www.zhihu.com/collection/1",
        client=client,
        writer=JsonlWriter(output),
        checkpoint_store=CheckpointStore(state_path),
    )

    lines = output.read_text(encoding="utf-8").splitlines()
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert result == ExportResult(exported=1, skipped=1)
    assert len(lines) == 2
    assert state["offset"] == 2
    assert state["exported"] == 2
