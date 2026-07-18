import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

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


def test_douyin_trial_defaults_to_twenty_and_external_temp(monkeypatch, tmp_path, capsys):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            counts={"selected": 20, "persisted": 18, "cleaned": 18},
            errors={"media_unavailable": 2},
            stopped=False,
            cleanup_pending=0,
        )

    monkeypatch.setattr("pkb.cli.run_douyin_trial", fake_run)
    exit_code = main(
        [
            "export",
            "douyin-favorites",
            "--output",
            str(tmp_path / "raw.jsonl"),
            "--state",
            str(tmp_path / "state.json"),
            "--report",
            str(tmp_path / "audit.json"),
        ]
    )

    assert exit_code == 0
    assert captured["limit"] == 20
    assert captured["request_delay"] >= 5
    assert captured["temp_root"].is_relative_to(Path(tempfile.gettempdir()))
    assert not captured["temp_root"].is_relative_to(Path.cwd())
    output = capsys.readouterr().out
    assert output == (
        "selected=20 persisted=18 cleaned=18 unavailable=0 failed=0 "
        "cleanup_pending=0 stopped=false errors=media_unavailable:2\n"
    )


def test_douyin_trial_rejects_limit_outside_one_to_twenty():
    assert main(["export", "douyin-favorites", "--limit", "0"]) == 2
    assert main(["export", "douyin-favorites", "--limit", "21"]) == 2


def test_douyin_trial_rejects_request_delay_below_five_seconds():
    assert main(["export", "douyin-favorites", "--request-delay", "4.9"]) == 2


def test_douyin_trial_passes_explicit_safe_paths(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        "pkb.cli.run_douyin_trial",
        lambda **kwargs: captured.update(kwargs)
        or SimpleNamespace(counts={}, errors={}, stopped=False, cleanup_pending=0),
    )
    paths = {
        "output": tmp_path / "raw.jsonl",
        "state": tmp_path / "state.json",
        "report": tmp_path / "audit.json",
        "temp_root": tmp_path / "temporary",
    }

    assert main(
        [
            "export", "douyin-favorites",
            "--limit", "1", "--request-delay", "9",
            "--output", str(paths["output"]),
            "--state", str(paths["state"]),
            "--report", str(paths["report"]),
            "--temp-root", str(paths["temp_root"]),
        ]
    ) == 0

    assert {key: captured[key] for key in paths} == paths
    assert captured["limit"] == 1
    assert captured["request_delay"] == 9


def test_douyin_trial_hides_exception_details(monkeypatch, capsys):
    secret = "sessionid=private-cookie local=C:/private/video.mp4"
    monkeypatch.setattr(
        "pkb.cli.run_douyin_trial",
        lambda **_: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    assert main(["export", "douyin-favorites"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Douyin trial stopped: internal_error\n"
    assert secret not in captured.err
