import json
from datetime import datetime, timedelta, timezone

import pytest

from pkb.derive.jobs import JobQueue
from pkb.knowledge.job_scopes import (
    JobScope,
    canonical_scope_json,
    canonical_scopes,
    scope_hash,
)
from pkb.knowledge.migrations import migrate


@pytest.fixture
def scoped_database(tmp_path):
    path = tmp_path / "scoped.db"
    import sqlite3

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    migrate(connection)
    connection.execute(
        """INSERT INTO documents
           (id, identity_key, source_content_hash, normalized_content_hash,
            normalization_version, schema_version)
           VALUES (1, 'doc:1', 'source', 'normalized', 1, 1)"""
    )
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def scoped_queue(scoped_database):
    with JobQueue(scoped_database) as queue:
        yield queue


def test_scope_hash_is_order_independent_but_role_and_ordinal_sensitive():
    scopes = (
        JobScope("fact", "42", "input", 0),
        JobScope("entity", "7", "anchor", 0),
    )
    assert scope_hash(scopes) == scope_hash(tuple(reversed(scopes)))
    assert scope_hash(scopes) != scope_hash(
        (JobScope("fact", "42", "context", 0), scopes[1])
    )
    assert canonical_scope_json(scopes) == (
        '[{"ordinal":0,"role":"anchor","type":"entity","id":"7"},'
        '{"ordinal":0,"role":"input","type":"fact","id":"42"}]'
    )


@pytest.mark.parametrize("scope_type", ["", "document ", "unknown"])
def test_scope_type_must_be_frozen_value(scope_type):
    with pytest.raises(ValueError):
        JobScope(scope_type, "1", "anchor", 0)


def test_scope_set_requires_exactly_one_anchor_and_unique_role_ordinal():
    with pytest.raises(ValueError, match="exactly one anchor"):
        scope_hash((JobScope("fact", "1", "input", 0),))
    with pytest.raises(ValueError, match="duplicate scope role/ordinal"):
        scope_hash(
            (
                JobScope("entity", "1", "anchor", 0),
                JobScope("fact", "2", "input", 0),
                JobScope("fact", "3", "input", 0),
            )
        )


@pytest.mark.parametrize("scope_role", ["", "anchor ", "unknown"])
def test_scope_role_must_be_frozen_value(scope_role):
    with pytest.raises(ValueError):
        JobScope("document", "1", scope_role, 0)


@pytest.mark.parametrize("scope_id", ["", " 1", "1 "])
def test_scope_id_must_be_non_empty_and_trimmed(scope_id):
    with pytest.raises(ValueError, match="scope_id"):
        JobScope("document", scope_id, "anchor", 0)


def test_scope_ordinal_must_be_non_negative():
    with pytest.raises(ValueError, match="non-negative"):
        JobScope("document", "1", "anchor", -1)


def test_enqueue_scoped_is_order_independent_and_reads_exact_scopes(scoped_queue):
    scopes = (
        JobScope("entity", "7", "anchor", 0),
        JobScope("document", "1", "input", 0),
    )

    first = scoped_queue.enqueue_scoped("fact-extraction", scopes, "hash", "v1")
    second = scoped_queue.enqueue_scoped(
        "fact-extraction", tuple(reversed(scopes)), "hash", "v1"
    )

    assert first == second
    assert scoped_queue.scopes(first) == canonical_scopes(scopes)


def test_legacy_enqueue_creates_document_anchor(scoped_queue):
    job_id = scoped_queue.enqueue("article", 1, "hash", "article-v1")

    assert scoped_queue.scopes(job_id) == (
        JobScope("document", "1", "anchor", 0),
    )
    job = scoped_queue.get(job_id)
    assert job.document_id == 1
    assert len(job.scope_hash) == 64


@pytest.mark.parametrize(
    ("job_type", "input_hash", "pipeline_version"),
    [("", "hash", "v1"), ("job", "", "v1"), ("job", "hash", "")],
)
def test_enqueue_scoped_rejects_empty_identity_parts(
    scoped_queue, job_type, input_hash, pipeline_version
):
    with pytest.raises(ValueError):
        scoped_queue.enqueue_scoped(
            job_type,
            (JobScope("entity", "7", "anchor", 0),),
            input_hash,
            pipeline_version,
        )


def test_document_anchor_requires_decimal_existing_document(scoped_queue):
    with pytest.raises(ValueError, match="decimal"):
        scoped_queue.enqueue_scoped(
            "job", (JobScope("document", "abc", "anchor", 0),), "hash", "v1"
        )
    with pytest.raises(ValueError, match="document"):
        scoped_queue.enqueue_scoped(
            "job", (JobScope("document", "99", "anchor", 0),), "hash", "v1"
        )


def test_scopes_rejects_unknown_job(scoped_queue):
    with pytest.raises(KeyError):
        scoped_queue.scopes(999)


def test_claim_can_filter_by_type_and_anchor_scope(scoped_queue):
    entity_job = scoped_queue.enqueue_scoped(
        "fact-maintenance", (JobScope("entity", "7", "anchor", 0),), "a", "v1"
    )
    scoped_queue.enqueue_scoped(
        "fact-maintenance", (JobScope("fact", "9", "anchor", 0),), "b", "v1"
    )

    claimed = scoped_queue.claim(
        "worker", job_type="fact-maintenance", anchor_type="entity"
    )

    assert claimed is not None and claimed.id == entity_job


def test_claim_rejects_unknown_anchor_type(scoped_queue):
    with pytest.raises(ValueError, match="anchor_type"):
        scoped_queue.claim("worker", anchor_type="unknown")


def test_claim_event_captures_scope_hash(scoped_queue):
    job_id = scoped_queue.enqueue_scoped(
        "fact-maintenance", (JobScope("entity", "7", "anchor", 0),), "a", "v1"
    )

    claimed = scoped_queue.claim("worker")

    assert claimed is not None
    events = scoped_queue.events(job_id)
    assert json.loads(events[0]["details_json"])["scope_hash"] == claimed.scope_hash
    assert json.loads(events[-1]["details_json"])["scope_hash"] == claimed.scope_hash


def test_expiry_and_dead_letter_events_capture_scope_hash(scoped_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = scoped_queue.enqueue_scoped(
        "fact-maintenance", (JobScope("entity", "7", "anchor", 0),), "a", "v1"
    )
    expected_hash = scoped_queue.get(job_id).scope_hash
    for attempt in range(3):
        claimed = scoped_queue.claim(
            f"worker-{attempt}",
            now=now + timedelta(seconds=attempt * 2),
            lease=timedelta(seconds=1),
        )
        assert claimed is not None and claimed.id == job_id

    assert scoped_queue.claim("last", now=now + timedelta(seconds=6)) is None
    audited = {
        event["event_type"]: json.loads(event["details_json"])["scope_hash"]
        for event in scoped_queue.events(job_id)
        if event["event_type"] in {"lease_expired", "dead_lettered"}
    }
    assert audited == {
        "lease_expired": expected_hash,
        "dead_lettered": expected_hash,
    }
