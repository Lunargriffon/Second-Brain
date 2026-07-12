import json
from pathlib import Path

from pkb.cli import main


def test_cli_exports_zhihu_batch_from_plain_url_file(tmp_path):
    collections_file = tmp_path / "zhihu-collections.txt"
    collections_file.write_text(
        "\n".join(
            [
                "# one URL per line",
                "https://www.zhihu.com/collection/656804809",
                "",
            ]
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "raw"
    state_dir = tmp_path / "state"

    exit_code = main(
        [
            "export",
            "zhihu-batch",
            "--collections-file",
            str(collections_file),
            "--output-dir",
            str(output_dir),
            "--state-dir",
            str(state_dir),
            "--fixture",
            "tests/fixtures/sample_collection.json",
        ]
    )

    output = output_dir / "zhihu-656804809.jsonl"
    state = state_dir / "zhihu-656804809.state.json"

    assert exit_code == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2
    assert json.loads(state.read_text(encoding="utf-8"))["offset"] == 2


def test_cli_rejects_batch_file_with_no_collection_urls(tmp_path):
    collections_file = tmp_path / "zhihu-collections.txt"
    collections_file.write_text("# empty\n", encoding="utf-8")

    exit_code = main(
        [
            "export",
            "zhihu-batch",
            "--collections-file",
            str(collections_file),
            "--output-dir",
            str(tmp_path / "raw"),
            "--state-dir",
            str(tmp_path / "state"),
        ]
    )

    assert exit_code == 1


def test_cli_audits_zhihu_collections_file_with_fake_fixture(tmp_path):
    collections_file = tmp_path / "zhihu-collections.txt"
    collections_file.write_text("https://www.zhihu.com/collection/656804809\n", encoding="utf-8")

    exit_code = main(
        [
            "audit",
            "zhihu",
            "--collections-file",
            str(collections_file),
            "--fixture",
            "tests/fixtures/sample_collection.json",
        ]
    )

    assert exit_code == 0


def test_cli_writes_zhihu_audit_report_jsonl_with_fake_fixture(tmp_path):
    collections_file = tmp_path / "zhihu-collections.txt"
    report = tmp_path / "audit.jsonl"
    collections_file.write_text("https://www.zhihu.com/collection/656804809\n", encoding="utf-8")

    exit_code = main(
        [
            "audit",
            "zhihu",
            "--collections-file",
            str(collections_file),
            "--fixture",
            "tests/fixtures/sample_collection.json",
            "--report",
            str(report),
        ]
    )

    assert exit_code == 0
    assert '"collection_id": "656804809"' in report.read_text(encoding="utf-8")
