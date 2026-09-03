from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from pkb.derive.jobs import JobQueue, LeaseOwnershipError
from pkb.knowledge.migrations import migrate


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "knowledge.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    migrate(connection)
    connection.execute(
        """INSERT INTO documents
           (identity_key, source_content_hash, normalized_content_hash,
            normalization_version, schema_version)
           VALUES ('doc:1', 'hash', 'normalized', 1, 1)"""
    )
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def job_queue(database):
    with JobQueue(database) as queue:
        yield queue


def test_expired_running_job_is_reclaimed(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = job_queue.enqueue("article", 1, "hash", "v1")
    first = job_queue.claim("worker-a", now=now, lease=timedelta(minutes=15))
    second = job_queue.claim(
        "worker-b", now=now + timedelta(minutes=16), lease=timedelta(minutes=15)
    )

    assert first is not None and second is not None
    assert first.id == second.id == job_id
    assert second.worker_id == "worker-b"
    assert second.attempts == 2
    events = job_queue.events(job_id)
    assert [event["event_type"] for event in events] == [
        "enqueued", "claimed", "lease_expired", "claimed"
    ]


def test_unexpired_lease_cannot_be_stolen_and_default_is_fifteen_minutes(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_queue.enqueue("article", 1, "hash", "v1")
    claimed = job_queue.claim("owner", now=now)

    assert claimed is not None
    assert claimed.lease_expires_at == now + timedelta(minutes=15)
    assert job_queue.claim("thief", now=now + timedelta(minutes=15) - timedelta(microseconds=1)) is None


def test_lease_boundary_is_expired(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_queue.enqueue("article", 1, "hash", "v1")
    first = job_queue.claim("owner", now=now)

    reclaimed = job_queue.claim("next", now=first.lease_expires_at)

    assert reclaimed is not None
    assert reclaimed.worker_id == "next"


def test_only_owner_can_heartbeat_succeed_or_fail(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = job_queue.enqueue("article", 1, "hash", "v1")
    job_queue.claim("owner", now=now)

    for operation in (
        lambda: job_queue.heartbeat(job_id, "other", now=now + timedelta(minutes=1)),
        lambda: job_queue.succeed(job_id, "other", now=now + timedelta(minutes=1)),
        lambda: job_queue.fail(job_id, "other", "boom", now=now + timedelta(minutes=1)),
    ):
        with pytest.raises(LeaseOwnershipError):
            operation()

    renewed = job_queue.heartbeat(job_id, "owner", now=now + timedelta(minutes=1))
    assert renewed.lease_expires_at == now + timedelta(minutes=16)
    job_queue.succeed(job_id, "owner", now=now + timedelta(minutes=2))
    assert job_queue.get(job_id).status == "succeeded"


def test_publish_and_succeed_is_one_transaction(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = job_queue.enqueue("article", 1, "publish-hash", "v1")
    job_queue.claim("owner", now=now)

    job_queue.publish_and_succeed(
        job_id,
        "owner",
        lambda connection: connection.execute(
            "UPDATE documents SET title='published' WHERE id=1"
        ),
        now=now + timedelta(minutes=1),
    )

    assert job_queue.get(job_id).status == "succeeded"
    assert job_queue.connection.execute(
        "SELECT title FROM documents WHERE id=1"
    ).fetchone()[0] == "published"


def test_lost_lease_blocks_publication_before_domain_write(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = job_queue.enqueue("article", 1, "lost-hash", "v1")
    job_queue.claim("old", now=now, lease=timedelta(seconds=1))
    job_queue.claim("new", now=now + timedelta(seconds=2))

    with pytest.raises(LeaseOwnershipError):
        job_queue.publish_and_succeed(
            job_id,
            "old",
            lambda connection: connection.execute(
                "UPDATE documents SET title='must-not-persist' WHERE id=1"
            ),
            now=now + timedelta(seconds=2),
        )

    assert job_queue.connection.execute(
        "SELECT title FROM documents WHERE id=1"
    ).fetchone()[0] is None


def test_publication_exception_rolls_back_domain_write_and_job_state(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = job_queue.enqueue("article", 1, "rollback-hash", "v1")
    job_queue.claim("owner", now=now)

    def broken_publish(connection):
        connection.execute("UPDATE documents SET title='rolled-back' WHERE id=1")
        raise RuntimeError("publication failed")

    with pytest.raises(RuntimeError, match="publication failed"):
        job_queue.publish_and_succeed(
            job_id, "owner", broken_publish, now=now + timedelta(minutes=1)
        )

    assert job_queue.connection.execute(
        "SELECT title FROM documents WHERE id=1"
    ).fetchone()[0] is None
    assert job_queue.get(job_id).status == "running"


def test_fail_marks_job_failed_and_records_error(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = job_queue.enqueue("article", 1, "hash", "v1")
    job_queue.claim("owner", now=now)

    job_queue.fail(job_id, "owner", "provider unavailable", now=now)

    job = job_queue.get(job_id)
    assert job.status == "failed"
    assert job.worker_id is None and job.lease_expires_at is None
    assert job.error == "provider unavailable"


def test_fourth_eligible_claim_dead_letters_and_continues(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    exhausted = job_queue.enqueue("article", 1, "old", "v1")
    following = job_queue.enqueue("article", 1, "new", "v1")
    for attempt in range(3):
        claimed = job_queue.claim(f"worker-{attempt}", now=now + timedelta(hours=attempt))
        assert claimed.id == exhausted

    claimed = job_queue.claim("final", now=now + timedelta(hours=4))

    assert claimed is not None and claimed.id == following
    assert job_queue.get(exhausted).status == "dead-letter"
    assert [e["event_type"] for e in job_queue.events(exhausted)][-1] == "dead_lettered"


def test_enqueue_is_compatible_and_idempotent_with_repository_job(database):
    with JobQueue(database) as queue:
        first = queue.enqueue("article", 1, "hash", "v1")
        second = queue.enqueue("article", 1, "hash", "v1")
        assert first == second
        assert queue.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_begin_immediate_prevents_two_connections_claiming_same_job(database):
    with JobQueue(database, timeout=0.05) as first, JobQueue(database, timeout=0.05) as second:
        job_id = first.enqueue("article", 1, "hash", "v1")
        first.connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second.claim("worker-b")
        first.connection.rollback()
        claimed = second.claim("worker-b")
        assert claimed is not None and claimed.id == job_id
