import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_douyin_all_selects_full_runner(monkeypatch, tmp_path, capsys):
    captured = {}
    monkeypatch.setattr(
        "pkb.cli._refresh_douyin_outputs", lambda: (True, True), raising=False
    )
    monkeypatch.setattr(
        "pkb.cli.run_douyin_full",
        lambda **kwargs: captured.update(kwargs)
        or SimpleNamespace(
            counts={"selected": 3, "persisted": 2, "cleaned": 2},
            errors={},
            stopped=False,
            cleanup_pending=0,
            discovered=3,
            discovery_complete=True,
        ),
    )

    assert main(
        [
            "export",
            "douyin-favorites",
            "--all",
            "--state",
            str(tmp_path / "state.json"),
            "--report",
            str(tmp_path / "audit.json"),
        ]
    ) == 0

    assert captured["request_delay"] == 7
    assert "limit" not in captured
    assert capsys.readouterr().out == (
        "selected=3 persisted=2 cleaned=2 unavailable=0 failed=0 "
        "cleanup_pending=0 stopped=false errors=- "
        "discovered=3 discovery_complete=true\n"
    )


def test_douyin_all_and_limit_are_mutually_exclusive(capsys):
    assert main(["export", "douyin-favorites", "--all", "--limit", "5"]) == 2
    assert "not allowed with argument --all" in capsys.readouterr().err


def test_douyin_all_refreshes_index_and_wiki_after_success(monkeypatch, tmp_path):
    calls = []
    report = tmp_path / "audit.json"
    monkeypatch.setattr(
        "pkb.cli.run_douyin_full",
        lambda **_: SimpleNamespace(
            counts={}, errors={}, stopped=False, cleanup_pending=0,
            discovered=2, discovery_complete=True,
            index_refreshed=False, wiki_refreshed=False,
        ),
    )
    monkeypatch.setattr(
        "pkb.cli._refresh_douyin_outputs",
        lambda: calls.append("refresh") or (True, True),
        raising=False,
    )

    assert main(["export", "douyin-favorites", "--all", "--report", str(report)]) == 0
    assert calls == ["refresh"]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["index_refreshed"] is True
    assert payload["wiki_refreshed"] is True


def test_douyin_all_does_not_refresh_after_run_stopper(monkeypatch, tmp_path):
    calls = []
    report = tmp_path / "audit.json"
    monkeypatch.setattr(
        "pkb.cli.run_douyin_full",
        lambda **_: SimpleNamespace(
            counts={}, errors={"auth_required": 1}, stopped=True,
            cleanup_pending=0, discovered=0, discovery_complete=False,
            index_refreshed=False, wiki_refreshed=False,
        ),
    )
    monkeypatch.setattr(
        "pkb.cli._refresh_douyin_outputs",
        lambda: calls.append("refresh") or (True, True),
        raising=False,
    )

    assert main(["export", "douyin-favorites", "--all", "--report", str(report)]) == 1
    assert calls == []
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["index_refreshed"] is False
    assert payload["wiki_refreshed"] is False


@pytest.mark.parametrize(
    ("refresh_result", "safe_code"),
    [
        ((False, False), "index_refresh_failed"),
        ((True, False), "wiki_refresh_failed"),
    ],
)
def test_douyin_all_refresh_failure_is_safe_and_returns_one(
    monkeypatch, tmp_path, capsys, refresh_result, safe_code
):
    report = tmp_path / "audit.json"
    secret = "C:/private/raw/sessionid=secret"
    monkeypatch.setattr(
        "pkb.cli.run_douyin_full",
        lambda **_: SimpleNamespace(
            counts={}, errors={}, stopped=False, cleanup_pending=0,
            discovered=1, discovery_complete=True,
            index_refreshed=False, wiki_refreshed=False,
        ),
    )
    monkeypatch.setattr(
        "pkb.cli._refresh_douyin_outputs", lambda: refresh_result, raising=False
    )

    assert main(
        ["export", "douyin-favorites", "--all", "--report", str(report)]
    ) == 1
    output = capsys.readouterr().out
    assert f"errors={safe_code}:1" in output
    assert secret not in output
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["errors"] == {safe_code: 1}
    assert payload["index_refreshed"] is refresh_result[0]
    assert payload["wiki_refreshed"] is refresh_result[1]


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
