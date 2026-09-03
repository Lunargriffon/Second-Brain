from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from pkb.derive.jobs import JobQueue, LeaseOwnershipError
from pkb.derive.pipeline import DerivationPipeline
from pkb.derive.provider import FakeDerivationProvider
from pkb.knowledge import migrations
from pkb.knowledge.job_scopes import JobScope, canonical_scopes
from pkb.knowledge.migrations import migrate


def _insert_document(connection: sqlite3.Connection, document_id: int) -> None:
    connection.execute(
        """INSERT INTO documents
           (id, identity_key, title, plain_content, source_content_hash,
            normalized_content_hash, normalization_version, schema_version)
           VALUES (?, ?, 'Article', 'Grounded source text', ?, ?, 1, 1)""",
        (
            document_id,
            f"doc:{document_id}",
            f"source-{document_id}",
            f"normalized-{document_id}",
        ),
    )


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "cross-domain.db"
    connection = sqlite3.connect(path)
    migrate(connection)
    _insert_document(connection, 1)
    _insert_document(connection, 2)
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def v7_article_database(tmp_path):
    path = tmp_path / "v7-article.db"
    connection = sqlite3.connect(path)
    with connection:
        for version in range(1, 8):
            for statement in migrations._MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {version}")
        _insert_document(connection, 1)
        connection.execute(
            """INSERT INTO jobs
               (id, job_type, document_id, input_hash, pipeline_version, status)
               VALUES (5, 'article', 1, 'source-1', 'article-v1', 'pending')"""
        )
        connection.execute(
            """INSERT INTO job_events (id, job_id, event_type, details_json)
               VALUES (8, 5, 'enqueued', '{"legacy":true}')"""
        )
    connection.close()
    return path


@pytest.fixture
def provider():
    return FakeDerivationProvider(
        [
            {
                "summary": "Grounded summary",
                "key_points": ["Grounded source text"],
                "topics": [{"name": "testing", "confidence": 0.9}],
                "tags": [{"name": "jobs", "confidence": 0.8}],
                "content_type": "tutorial",
                "evergreen_score": 4,
                "reading_priority": 3,
                "priority_reason": "acceptance",
                "source_citations": [
                    {"claim": "grounded", "excerpt": "Grounded source text"}
                ],
            }
        ]
    )


def _event_types(queue: JobQueue, job_id: int) -> list[str]:
    return [str(row["event_type"]) for row in queue.events(job_id)]


def test_document_article_job_remains_backward_compatible(database):
    with JobQueue(database) as queue:
        job_id = queue.enqueue("article", 1, "source-1", "article-v1")
        assert queue.get(job_id).status == "pending"
        assert queue.scopes(job_id) == (JobScope("document", "1", "anchor", 0),)
        claimed = queue.claim("article-worker", job_type="article")
        assert claimed is not None and claimed.id == job_id
        assert queue.scopes(job_id) == (JobScope("document", "1", "anchor", 0),)
        assert _event_types(queue, job_id) == ["enqueued", "claimed"]
        assert queue.get(job_id).status == "running"


def test_entity_anchor_can_reference_multiple_document_inputs(database):
    scopes = (
        JobScope("entity", "7", "anchor", 0),
        JobScope("document", "1", "input", 0),
        JobScope("document", "2", "input", 1),
    )
    with JobQueue(database) as queue:
        job_id = queue.enqueue_scoped("entity-maintenance", scopes, "entity-7", "v1")
        assert queue.get(job_id).document_id is None
        assert queue.scopes(job_id) == canonical_scopes(scopes)
        claimed = queue.claim("entity-worker", anchor_type="entity")
        assert claimed is not None and claimed.id == job_id
        queue.publish_and_succeed(
            job_id,
            "entity-worker",
            lambda connection: connection.execute(
                """INSERT INTO knowledge_events
                   (object_type, object_id, event_type, actor_type, reason, job_id)
                   VALUES ('entity', 7, 'rebuilt', 'system', 'acceptance', ?)""",
                (job_id,),
            ),
        )
        assert queue.scopes(job_id) == canonical_scopes(scopes)
        assert _event_types(queue, job_id) == ["enqueued", "claimed", "succeeded"]
        assert queue.get(job_id).status == "succeeded"


def test_fact_relation_anchor_is_idempotent_across_scope_order(database):
    scopes = (
        JobScope("fact_relation", "11", "anchor", 0),
        JobScope("fact", "3", "input", 0),
        JobScope("fact", "4", "input", 1),
    )
    with JobQueue(database) as queue:
        first = queue.enqueue_scoped("relation-validation", scopes, "relation-11", "v1")
        second = queue.enqueue_scoped(
            "relation-validation", tuple(reversed(scopes)), "relation-11", "v1"
        )
        assert first == second
        assert queue.scopes(first) == canonical_scopes(scopes)
        assert _event_types(queue, first) == ["enqueued"]
        claimed = queue.claim("relation-worker", anchor_type="fact_relation")
        assert claimed is not None and claimed.id == first
        queue.publish_and_succeed(first, "relation-worker", lambda _connection: None)
        assert queue.scopes(first) == canonical_scopes(scopes)
        assert _event_types(queue, first) == ["enqueued", "claimed", "succeeded"]
        assert queue.get(first).status == "succeeded"


def test_expired_cross_domain_job_is_reclaimed_and_old_worker_cannot_publish(database):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scopes = (JobScope("entity", "7", "anchor", 0),)
    with JobQueue(database) as queue:
        job_id = queue.enqueue_scoped("entity-maintenance", scopes, "a", "v1")
        queue.claim("old", now=now, lease=timedelta(seconds=1))
        queue.claim("new", now=now + timedelta(seconds=2))
        with pytest.raises(LeaseOwnershipError):
            queue.publish_and_succeed(
                job_id,
                "old",
                lambda connection: connection.execute(
                    """INSERT INTO knowledge_events
                       (object_type, object_id, event_type, actor_type, reason, job_id)
                       VALUES ('entity', 7, 'rebuilt', 'system', 'acceptance', ?)""",
                    (job_id,),
                ),
                now=now + timedelta(seconds=2),
            )
        assert queue.scopes(job_id) == scopes
        assert _event_types(queue, job_id) == [
            "enqueued",
            "claimed",
            "lease_expired",
            "claimed",
        ]
        assert queue.get(job_id).status == "running"
        assert queue.get(job_id).worker_id == "new"
        assert queue.connection.execute(
            "SELECT COUNT(*) FROM knowledge_events WHERE job_id=?", (job_id,)
        ).fetchone()[0] == 0


def test_v7_database_upgrades_and_existing_article_pipeline_still_succeeds(
    v7_article_database, provider, tmp_path
):
    pipeline = DerivationPipeline(
        v7_article_database, provider, run_log=tmp_path / "article-run.jsonl"
    )
    result = pipeline.run(limit=1)
    assert result.succeeded == 1
    with JobQueue(v7_article_database) as queue:
        assert queue.connection.execute("PRAGMA user_version").fetchone()[0] == 8
        assert queue.scopes(5) == (JobScope("document", "1", "anchor", 0),)
        assert _event_types(queue, 5) == [
            "enqueued",
            "claimed",
            "heartbeat",
            "heartbeat",
            "succeeded",
        ]
        assert queue.get(5).status == "succeeded"
