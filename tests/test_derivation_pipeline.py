from __future__ import annotations

import json
import sqlite3

import pytest

from pkb.derive.jobs import JobQueue, LeaseOwnershipError
from pkb.derive.pipeline import DerivationPipeline
from pkb.derive.provider import (
    FakeDerivationProvider,
    ProviderAuthError,
    ProviderInvalidRequestError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from pkb.knowledge.migrations import migrate


def payload(summary: str = "先理解再练习") -> dict[str, object]:
    return {
        "summary": summary,
        "key_points": ["先理解"],
        "topics": [{"name": "学习", "confidence": 0.9}],
        "tags": [{"name": "方法", "confidence": 0.8}],
        "content_type": "tutorial",
        "evergreen_score": 4,
        "reading_priority": 3,
        "priority_reason": "可复用",
        "source_citations": [{"claim": "先理解", "excerpt": "先理解，再练习"}],
    }


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "knowledge.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    for identity in ("doc:1", "doc:2"):
        connection.execute(
            """INSERT INTO documents
               (identity_key, title, plain_content, source_content_hash,
                normalized_content_hash, normalization_version, schema_version)
               VALUES (?, '学习文章', '先理解，再练习', ?, ?, 1, 1)""",
            (identity, f"source-{identity}", f"normalized-{identity}"),
        )
    connection.commit()
    connection.close()
    with JobQueue(path) as queue:
        queue.enqueue("article", 1, "source-doc:1", "article-v1")
        queue.enqueue("article", 2, "source-doc:2", "article-v1")
    return path


def rows(database, sql: str):
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(sql).fetchall()


def test_pipeline_is_bounded_and_does_not_call_provider_twice_for_same_input(database, tmp_path):
    provider = FakeDerivationProvider([payload(), payload("第二篇")])
    pipeline = DerivationPipeline(database, provider, run_log=tmp_path / "runs.jsonl")

    first = pipeline.run(limit=1)
    second = pipeline.run(limit=1)
    third = pipeline.run(limit=1)

    assert (first.processed, first.succeeded) == (1, 1)
    assert (second.processed, second.succeeded) == (1, 1)
    assert third.processed == 0
    assert len(provider.requests) == 2
    assert len(rows(database, "SELECT * FROM derivations")) == 2


def test_success_promotes_valid_result_and_updates_search_and_labels(database, tmp_path):
    pipeline = DerivationPipeline(
        database, FakeDerivationProvider([payload()]), run_log=tmp_path / "runs.jsonl"
    )

    result = pipeline.run(limit=1)

    derivation = rows(database, "SELECT * FROM derivations")[0]
    search = rows(database, "SELECT summary, tags FROM documents_search_content WHERE document_id=1")[0]
    assert result.succeeded == 1
    assert derivation["source_content_hash"] == "source-doc:1"
    assert derivation["normalized_content_hash"] == "normalized-doc:1"
    assert derivation["normalization_version"] == 1
    assert json.loads(derivation["payload_json"])["summary"] == "先理解再练习"
    assert (search["summary"], search["tags"]) == ("先理解再练习", "方法")
    assert rows(database, "SELECT status FROM jobs WHERE document_id=1")[0]["status"] == "succeeded"
    assert rows(database, "SELECT origin FROM document_tags")[0]["origin"] == "ai"


def test_invalid_derivation_is_not_promoted_and_batch_continues(database, tmp_path):
    invalid = payload()
    invalid["source_citations"] = [{"claim": "虚构", "excerpt": "不在原文"}]
    provider = FakeDerivationProvider([invalid, payload("第二篇")])

    result = DerivationPipeline(database, provider, run_log=tmp_path / "runs.jsonl").run(limit=2)

    assert (result.processed, result.succeeded, result.failed, result.stopped) == (2, 1, 1, False)
    assert len(rows(database, "SELECT * FROM derivations")) == 1
    assert [r["status"] for r in rows(database, "SELECT status FROM jobs ORDER BY id")] == ["failed", "succeeded"]


def test_malformed_model_response_fails_one_job_and_continues(database, tmp_path):
    provider = FakeDerivationProvider(
        [ProviderMalformedResponseError("private malformed body"), payload("第二篇")]
    )

    result = DerivationPipeline(database, provider, run_log=tmp_path / "runs.jsonl").run(limit=2)

    assert (result.processed, result.succeeded, result.failed, result.stopped) == (2, 1, 1, False)
    assert len(rows(database, "SELECT * FROM derivations")) == 1


@pytest.mark.parametrize("error", [ProviderAuthError("secret-key"), ProviderInvalidRequestError("bad body")])
def test_permanent_provider_error_stops_batch_without_leaking_details(database, tmp_path, error):
    log = tmp_path / "runs.jsonl"
    provider = FakeDerivationProvider([error, payload("must not run")])

    result = DerivationPipeline(database, provider, run_log=log).run(limit=2)

    assert (result.processed, result.failed, result.stopped) == (1, 1, True)
    assert len(provider.requests) == 1
    persisted = json.dumps([dict(r) for r in rows(database, "SELECT error FROM jobs")]) + log.read_text()
    assert "secret-key" not in persisted and "bad body" not in persisted
    assert len(rows(database, "SELECT * FROM derivations")) == 0


@pytest.mark.parametrize("temporary", [ProviderRateLimitError("429 body"), ProviderTemporaryError("500 body")])
def test_temporary_provider_error_retries_within_current_lease(database, tmp_path, temporary):
    provider = FakeDerivationProvider([temporary, temporary, payload()])

    result = DerivationPipeline(
        database, provider, run_log=tmp_path / "runs.jsonl", retry_delays=(0, 0)
    ).run(limit=1)

    assert (result.succeeded, result.failed, result.stopped) == (1, 0, False)
    assert len(provider.requests) == 3
    events = rows(database, "SELECT event_type FROM job_events WHERE job_id=1 ORDER BY id")
    assert [r["event_type"] for r in events].count("heartbeat") == 4


def test_exhausted_temporary_error_fails_job_and_stops_batch(database, tmp_path):
    provider = FakeDerivationProvider([ProviderTemporaryError("private response")] * 3)

    result = DerivationPipeline(
        database, provider, run_log=tmp_path / "runs.jsonl", retry_delays=(0, 0)
    ).run(limit=2)

    assert (result.processed, result.failed, result.stopped) == (1, 1, True)
    assert len(provider.requests) == 3
    assert rows(database, "SELECT error FROM jobs WHERE id=1")[0]["error"] == "provider_temporary"


def test_run_log_is_append_only_and_contains_no_source_or_provider_payload(database, tmp_path):
    log = tmp_path / "runs.jsonl"
    provider = FakeDerivationProvider([payload()])
    DerivationPipeline(database, provider, run_log=log).run(limit=1)

    record = json.loads(log.read_text(encoding="utf-8").strip())
    serialized = json.dumps(record, ensure_ascii=False)
    assert record["status"] == "validated"
    assert "先理解，再练习" not in serialized
    assert "先理解再练习" not in serialized
    assert "requests" not in serialized


def test_limit_must_be_positive(database, tmp_path):
    pipeline = DerivationPipeline(database, FakeDerivationProvider([]), run_log=tmp_path / "runs.jsonl")
    with pytest.raises(ValueError, match="positive"):
        pipeline.run(limit=0)


def test_pipeline_claims_only_article_jobs(database, tmp_path):
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE jobs SET job_type='relation' WHERE id=1")
        connection.commit()
    provider = FakeDerivationProvider([payload("article only")])

    result = DerivationPipeline(database, provider, run_log=tmp_path / "runs.jsonl").run(limit=1)

    assert result.succeeded == 1
    assert rows(database, "SELECT status FROM jobs WHERE id=1")[0]["status"] == "pending"


@pytest.mark.parametrize("crash_stage", ["derivation", "projection"])
def test_article_publication_rolls_back_when_stage_fails(
    database, tmp_path, crash_stage
):
    def crash(stage: str) -> None:
        if stage == crash_stage:
            raise RuntimeError("simulated crash")

    provider = FakeDerivationProvider([payload()])
    pipeline = DerivationPipeline(
        database, provider, run_log=tmp_path / "runs.jsonl", stage_hook=crash
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        pipeline.run(limit=1)
    assert rows(database, "SELECT * FROM derivations") == []
    assert rows(database, "SELECT * FROM document_tags") == []
    assert rows(database, "SELECT * FROM document_topics") == []
    assert rows(
        database, "SELECT * FROM documents_search_content WHERE document_id=1"
    ) == []
    assert rows(database, "SELECT status FROM jobs WHERE id=1")[0]["status"] == "running"
    assert rows(
        database,
        "SELECT * FROM job_events WHERE job_id=1 AND event_type='succeeded'",
    ) == []
    assert len(provider.requests) == 1


def test_new_derivation_replaces_current_ai_label_projection_but_keeps_history(database, tmp_path):
    log = tmp_path / "runs.jsonl"
    DerivationPipeline(database, FakeDerivationProvider([payload()]), run_log=log).run(limit=1)
    changed = payload("更新摘要")
    changed["tags"] = [{"name": "新标签", "confidence": 0.7}]
    changed["topics"] = [{"name": "新主题", "confidence": 0.7}]
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("DELETE FROM jobs WHERE document_id=2")
        connection.execute(
            "UPDATE documents SET source_content_hash='source-new' WHERE id=1"
        )
        connection.commit()
    with JobQueue(database) as queue:
        queue.enqueue("article", 1, "source-new", "article-v1")

    DerivationPipeline(database, FakeDerivationProvider([changed]), run_log=log).run(limit=1)

    assert len(rows(database, "SELECT * FROM derivations WHERE document_id=1")) == 2
    assert [r["display_name"] for r in rows(
        database,
        "SELECT t.display_name FROM document_tags dt JOIN tags t ON t.id=dt.tag_id WHERE dt.document_id=1 AND dt.origin='ai'",
    )] == ["新标签"]
    assert [r["display_name"] for r in rows(
        database,
        "SELECT t.display_name FROM document_topics dt JOIN topics t ON t.id=dt.topic_id WHERE dt.document_id=1 AND dt.origin='ai'",
    )] == ["新主题"]


def test_expired_lease_after_provider_call_cannot_promote_result(database, tmp_path):
    class ExpiringProvider:
        provider_name = "expiring"
        model_name = "test"

        def complete(self, *, system, user):
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00Z' WHERE id=1"
                )
                connection.commit()
            return payload()

    with pytest.raises(LeaseOwnershipError):
        DerivationPipeline(
            database, ExpiringProvider(), run_log=tmp_path / "runs.jsonl"
        ).run(limit=1)

    assert rows(database, "SELECT * FROM derivations") == []
    assert rows(database, "SELECT * FROM document_tags") == []
    assert rows(database, "SELECT * FROM document_topics") == []
    assert rows(
        database, "SELECT * FROM documents_search_content WHERE document_id=1"
    ) == []
    assert rows(
        database,
        "SELECT * FROM job_events WHERE job_id=1 AND event_type='succeeded'",
    ) == []
