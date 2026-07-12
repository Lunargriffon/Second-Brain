import json

from pkb.cli import main


def test_cli_exports_zhihu_collection_with_fake_client(tmp_path):
    output = tmp_path / "data" / "raw" / "zhihu.jsonl"
    state = tmp_path / "data" / "state" / "zhihu.state.json"

    exit_code = main(
        [
            "export",
            "zhihu",
            "--collection-url",
            "https://www.zhihu.com/collection/1",
            "--output",
            str(output),
            "--state",
            str(state),
            "--fixture",
            "tests/fixtures/sample_collection.json",
        ]
    )

    assert exit_code == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2
    assert json.loads(state.read_text(encoding="utf-8"))["offset"] == 2


def test_cli_accepts_real_client_safety_limit_without_phase_two_commands(tmp_path):
    exit_code = main(
        [
            "export",
            "zhihu",
            "--collection-url",
            "https://www.zhihu.com/collection/1",
            "--output",
            str(tmp_path / "zhihu.jsonl"),
            "--env",
            str(tmp_path / "missing.env"),
            "--limit",
            "1",
        ]
    )

    assert exit_code == 1


def test_cli_rejects_zhihu_author_without_cookie(tmp_path):
    exit_code = main(
        [
            "export",
            "zhihu-author",
            "--author-url",
            "https://www.zhihu.com/people/18868-42",
            "--output",
            str(tmp_path / "zhihu-author.jsonl"),
            "--env",
            str(tmp_path / "missing.env"),
            "--limit",
            "1",
        ]
    )

    assert exit_code == 1


def test_cli_rejects_unknown_phase_two_commands():
    exit_code = main(["summarize"])

    assert exit_code == 2
