import json
import sqlite3
from pathlib import Path

from tools.acceptance_second_brain import _logical_hash, main, run_acceptance


FIXTURE_RAW = Path("tests/fixtures/knowledge")


def test_fixture_acceptance_is_reproducible(tmp_path: Path):
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    (frozen / "evidence.bin").write_bytes(b"immutable fixture evidence\n")
    first = run_acceptance(
        FIXTURE_RAW,
        tmp_path / "one",
        frozen_dir=frozen,
        accepted_queries=("学习",),
    )
    second = run_acceptance(
        FIXTURE_RAW,
        tmp_path / "two",
        frozen_dir=frozen,
        accepted_queries=("学习",),
    )

    assert first.status == "ok"
    assert first.logical_hash == second.logical_hash
    assert first.raw_hash_before == first.raw_hash_after
    assert first.frozen_hash_before == first.frozen_hash_after
    assert first.document_count > 0
    # Cross-source observations can legitimately preserve multiple historical
    # derivations for one canonical document.
    assert first.derivation_count >= first.document_count
    assert first.search_checks_passed == first.search_checks_run == 1
    assert first.vault_check_ok is True
    assert first.second_pass_zero_work is True


def test_acceptance_report_contains_only_aggregate_results(tmp_path: Path):
    report_path = tmp_path / "acceptance.json"

    result = run_acceptance(
        FIXTURE_RAW,
        tmp_path / "work",
        accepted_queries=("学习",),
    )
    result.write(report_path)

    report = report_path.read_text(encoding="utf-8")
    assert '"status": "ok"' in report
    assert "title" not in report
    assert "content" not in report
    assert "document_id" not in report

    database = tmp_path / "work" / "knowledge.db"
    vault = tmp_path / "work" / "vault"
    logical_before = _logical_hash(database, vault)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE derivations SET id='local-' || id")
    assert _logical_hash(database, vault) == logical_before


def test_empty_source_text_is_an_explained_aggregate_failure(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    record = json.loads(
        (FIXTURE_RAW / "x-bookmarks.jsonl").read_text(encoding="utf-8")
    )
    record["text"] = ""
    (raw / "x-bookmarks.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    result = run_acceptance(raw, tmp_path / "work")

    assert result.status == "failed"
    assert "documents_without_source_text" in result.failures
    assert "deterministic_derivation_skipped_empty_source_text" in result.skips


def test_no_supported_documents_is_an_explained_failure(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "unsupported.jsonl").write_text("{}\n", encoding="utf-8")

    result = run_acceptance(raw, tmp_path / "work")

    assert result.status == "failed"
    assert "no_supported_documents" in result.failures
    assert result.document_count == 0
    assert result.logical_hash


def test_cli_releases_temporary_database_before_cleanup(tmp_path: Path):
    report = tmp_path / "acceptance.json"

    assert main([
        "--raw-dir", str(FIXTURE_RAW),
        "--output", str(report),
        "--accepted-query", "学习",
    ]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "ok"
