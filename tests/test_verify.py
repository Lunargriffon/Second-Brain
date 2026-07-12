import json

from pkb.verify import verify_zhihu_exports


def test_verify_reports_count_match(tmp_path):
    audit = tmp_path / "audit.jsonl"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    audit.write_text(
        json.dumps({"collection_id": "575638886", "scanned_total": 2}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (raw_dir / "zhihu-575638886.jsonl").write_text('{"id": "a"}\n{"id": "b"}\n', encoding="utf-8")

    results = verify_zhihu_exports(audit, raw_dir)

    assert results[0].status == "ok"
    assert results[0].scanned_total == 2
    assert results[0].exported_total == 2


def test_verify_reports_count_mismatch(tmp_path):
    audit = tmp_path / "audit.jsonl"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    audit.write_text(
        json.dumps({"collection_id": "575638886", "scanned_total": 3}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (raw_dir / "zhihu-575638886.jsonl").write_text('{"id": "a"}\n', encoding="utf-8")

    results = verify_zhihu_exports(audit, raw_dir)

    assert results[0].status == "mismatch"
    assert results[0].exported_total == 1
