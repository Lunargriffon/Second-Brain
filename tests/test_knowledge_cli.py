from __future__ import annotations

import json
from pathlib import Path

from pkb.cli import main


def _write_fixture(raw_dir: Path) -> None:
    raw_dir.mkdir()
    records = [
        {
            "id": "zhihu_answer_123",
            "title": "学习方法",
            "author": "甲",
            "content": "先理解，再练习。",
            "url": "https://www.zhihu.com/api/v4/answers/123",
            "created_at": "2025-01-01T00:00:00Z",
            "images": [],
        }
    ]
    (raw_dir / "zhihu-10.jsonl").write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def test_index_build_and_json_search(tmp_path: Path, capsys) -> None:
    raw_dir = tmp_path / "raw"
    _write_fixture(raw_dir)
    db = tmp_path / "knowledge.db"

    assert main(["index", "build", "--raw-dir", str(raw_dir), "--db", str(db)]) == 0
    assert main(["search", "学习", "--db", str(db), "--format", "json"]) == 0

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload[0]["title"] == "学习方法"
    assert payload[0]["memberships"][0]["source"] == "zhihu"


def test_index_report_status_and_rebuild(tmp_path: Path, capsys) -> None:
    raw_dir = tmp_path / "raw"
    _write_fixture(raw_dir)
    db = tmp_path / "knowledge.db"
    report = tmp_path / "reports" / "index.json"

    assert main(["index", "build", "--raw-dir", str(raw_dir), "--db", str(db), "--strict", "--report", str(report)]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["created"] == 1
    assert main(["index", "status", "--db", str(db), "--format", "json"]) == 0
    status = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert status["documents"] == 1
    assert main(["index", "rebuild", "--raw-dir", str(raw_dir), "--db", str(db)]) == 0


def test_search_text_format_and_filters(tmp_path: Path, capsys) -> None:
    raw_dir = tmp_path / "raw"
    _write_fixture(raw_dir)
    db = tmp_path / "knowledge.db"
    assert main(["index", "build", "--raw-dir", str(raw_dir), "--db", str(db)]) == 0
    capsys.readouterr()

    assert main(["search", "学习", "--db", str(db), "--source", "zhihu", "--collection", "10", "--limit", "1"]) == 0
    assert "学习方法" in capsys.readouterr().out

