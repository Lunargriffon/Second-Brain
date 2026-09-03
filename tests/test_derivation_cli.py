from __future__ import annotations

import json
import sqlite3

import pytest

from pkb.cli import main
from pkb.derive.jobs import JobQueue
from pkb.derive.provider import FakeDerivationProvider
from pkb.knowledge.migrations import migrate


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "knowledge.db"
    connection = sqlite3.connect(path)
    migrate(connection)
    for number, version in ((1, 2), (2, 2)):
        connection.execute(
            """INSERT INTO documents
               (identity_key, title, plain_content, source_content_hash,
                normalized_content_hash, normalization_version, schema_version)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (f"doc:{number}", f"Title {number}", "source text", f"source-{number}",
             f"normalized-{number}", version),
        )
    connection.execute(
        """INSERT INTO derivations
           (id, document_id, kind, payload_json, input_hash, source_content_hash,
            normalized_content_hash, normalization_version, schema_version,
            prompt_version, provider, model, generation_parameters_json, status)
           VALUES ('old', 1, 'article', '{}', 'source-1', 'source-1',
                   'old-normalized', 1, 1, 'article-v1', 'fake', 'fake-model', '{}', 'accepted')"""
    )
    connection.commit()
    connection.close()
    return path


def count_jobs(db, status=None):
    with sqlite3.connect(db) as connection:
        if status:
            return connection.execute("SELECT count(*) FROM jobs WHERE status=?", (status,)).fetchone()[0]
        return connection.execute("SELECT count(*) FROM jobs").fetchone()[0]


def test_normalization_migration_dry_run_queues_nothing(db, capsys):
    code = main(["derive", "migrate", "--db", str(db),
                 "--normalization-version", "2", "--limit", "10", "--dry-run"])
    assert code == 0
    assert "would_queue=1" in capsys.readouterr().out
    assert count_jobs(db) == 0


def test_migration_explicitly_queues_stale_derivation(db, capsys):
    code = main(["derive", "migrate", "--db", str(db),
                 "--normalization-version", "2", "--limit", "1"])
    assert code == 0
    assert "queued=1" in capsys.readouterr().out
    with sqlite3.connect(db) as connection:
        row = connection.execute("SELECT input_hash, pipeline_version FROM jobs").fetchone()
    assert row[1] == "article-v1"
    assert len(row[0]) == 64 and row[0] not in {"source-1", "normalized-1"}

    with sqlite3.connect(db) as connection:
        connection.execute(
            """INSERT INTO derivations
               (id, document_id, kind, payload_json, input_hash, source_content_hash,
                normalized_content_hash, normalization_version, schema_version,
                prompt_version, provider, model, generation_parameters_json, status)
               VALUES ('old-2', 2, 'article', '{}', 'source-2', 'source-2',
                       'old-normalized-2', 1, 1, 'article-v1', 'fake', 'fake-model', '{}', 'accepted')"""
        )
        connection.commit()
    assert main(["derive", "migrate", "--db", str(db),
                 "--normalization-version", "2", "--limit", "1", "--dry-run"]) == 0
    assert "would_queue=1" in capsys.readouterr().out
    assert main(["derive", "migrate", "--db", str(db),
                 "--normalization-version", "2", "--limit", "1"]) == 0
    capsys.readouterr()
    assert main(["derive", "migrate", "--db", str(db),
                 "--normalization-version", "2", "--limit", "1", "--dry-run"]) == 0
    assert "would_queue=0" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["articles", "migrate", "retry"])
def test_bounded_commands_reject_zero_limit(db, capsys, command):
    args = ["derive", command, "--db", str(db), "--limit", "0"]
    if command == "migrate":
        args += ["--normalization-version", "2"]
    if command == "retry":
        args += ["--status", "failed"]
    assert main(args) == 2
    assert "limit must be positive" in capsys.readouterr().err


def test_limit_and_unlimited_are_mutually_exclusive(db, capsys):
    assert main(["derive", "articles", "--db", str(db), "--limit", "1", "--unlimited"]) == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_articles_dry_run_does_not_claim_call_or_write(db, tmp_path, capsys):
    with JobQueue(db) as queue:
        queue.enqueue("article", 1, "source-1", "article-v1")
    provider = FakeDerivationProvider([])
    before = db.read_bytes()
    code = main(["derive", "articles", "--db", str(db), "--limit", "1",
                 "--dry-run", "--report", str(tmp_path / "report.json")], provider=provider)
    output = capsys.readouterr().out
    assert code == 0
    assert "documents=1" in output and "model=fake-model" in output
    assert "prompt_version=article-v1" in output and "estimated_input_chars=11" in output
    assert provider.requests == []
    assert db.read_bytes() == before
    assert not (tmp_path / "report.json").exists()


def test_articles_missing_provider_config_is_sanitized(db, tmp_path, capsys):
    code = main(["derive", "articles", "--db", str(db), "--limit", "1",
                 "--dry-run", "--env", str(tmp_path / "missing.env")])
    assert code == 1
    output = capsys.readouterr()
    assert "configuration" in output.err.lower()
    assert output.out == ""


def test_articles_uses_documented_llm_env_without_leaking_key(db, tmp_path, capsys, monkeypatch):
    for name in ("PKB_LLM_BASE_URL", "PKB_LLM_API_KEY", "PKB_LLM_MODEL", "PKB_LLM_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "PKB_LLM_BASE_URL=https://llm.invalid/v1\n"
        "PKB_LLM_API_KEY=super-secret-key\n"
        "PKB_LLM_MODEL=review-model\n"
        "PKB_LLM_TIMEOUT=15\n",
        encoding="utf-8",
    )
    assert main(["derive", "articles", "--db", str(db), "--limit", "1",
                 "--dry-run", "--env", str(env)]) == 0
    output = capsys.readouterr()
    assert "model=review-model" in output.out
    assert "super-secret-key" not in output.out + output.err


def test_status_json_reports_sanitized_counts(db, capsys):
    with JobQueue(db) as queue:
        job_id = queue.enqueue("article", 1, "source-1", "article-v1")
        queue.claim("worker")
        queue.fail(job_id, "worker", "private provider body")
    assert main(["derive", "status", "--db", str(db), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed"] == 1
    assert "private" not in json.dumps(payload)


def test_retry_resets_only_bounded_failed_jobs(db, capsys):
    with JobQueue(db) as queue:
        ids = [queue.enqueue("article", n, f"input-{n}", "article-v1") for n in (1, 2)]
        for job_id in ids:
            job = queue.claim("worker")
            queue.fail(job.id, "worker", "sanitized")
    assert main(["derive", "retry", "--db", str(db), "--status", "failed", "--limit", "1"]) == 0
    assert "retried=1" in capsys.readouterr().out
    assert count_jobs(db, "pending") == 1 and count_jobs(db, "failed") == 1


def test_retry_never_touches_succeeded_or_dead_letter_jobs(db):
    with JobQueue(db) as queue:
        succeeded = queue.enqueue("article", 1, "succeeded-input", "article-v1")
        job = queue.claim("worker")
        queue.succeed(job.id, "worker")
        dead = queue.enqueue("article", 2, "dead-input", "article-v1")
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE jobs SET status='dead-letter', attempts=3 WHERE id=?", (dead,))
        connection.commit()
    assert main(["derive", "retry", "--db", str(db), "--status", "failed", "--unlimited"]) == 0
    with sqlite3.connect(db) as connection:
        rows = dict(connection.execute("SELECT id, status FROM jobs WHERE id IN (?, ?)",
                                       (succeeded, dead)).fetchall())
    assert rows == {succeeded: "succeeded", dead: "dead-letter"}


def test_retry_requires_explicit_failed_status(db):
    assert main(["derive", "retry", "--db", str(db), "--limit", "1"]) == 2
