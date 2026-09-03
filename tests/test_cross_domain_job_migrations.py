import sqlite3

import pytest

from pkb.knowledge import migrations
from pkb.knowledge.migrations import migrate


def rows(connection, sql):
    return connection.execute(sql).fetchall()


@pytest.fixture
def v7_database(tmp_path):
    connection = sqlite3.connect(tmp_path / "v7.db")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    with connection:
        for version in range(1, 8):
            for statement in migrations._MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {version}")
        connection.execute(
            """INSERT INTO documents
               (id, identity_key, title, source_content_hash,
                normalized_content_hash, normalization_version, schema_version)
               VALUES (17, 'legacy', 'Legacy', 'source', 'normalized', 1, 1)"""
        )
        connection.execute(
            """INSERT INTO jobs
               (id, job_type, document_id, input_hash, pipeline_version, status,
                created_at, updated_at)
               VALUES (23, 'article', 17, 'input', 'article-v1', 'pending',
                       '2026-01-01 00:00:00', '2026-01-01 00:00:00')"""
        )
        connection.execute(
            """INSERT INTO job_events
               (id, job_id, event_type, details_json, created_at)
               VALUES (31, 23, 'enqueued', '{"legacy":true}',
                       '2026-01-01 00:00:01')"""
        )
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def knowledge_connection(tmp_path):
    connection = sqlite3.connect(tmp_path / "cross-domain.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def test_v8_jobs_support_non_document_anchor(knowledge_connection):
    columns = {
        row[1]: row for row in knowledge_connection.execute("PRAGMA table_info(jobs)")
    }
    assert columns["document_id"][3] == 0
    assert columns["scope_hash"][3] == 1
    assert knowledge_connection.execute("PRAGMA user_version").fetchone()[0] == 8


def test_v7_to_v8_preserves_job_and_event_ids_and_adds_document_anchor(v7_database):
    before_jobs = rows(v7_database, "SELECT * FROM jobs ORDER BY id")
    before_events = rows(v7_database, "SELECT * FROM job_events ORDER BY id")

    migrate(v7_database)

    after_jobs = rows(v7_database, "SELECT * FROM jobs ORDER BY id")
    after_events = rows(v7_database, "SELECT * FROM job_events ORDER BY id")
    assert [row["id"] for row in after_jobs] == [row["id"] for row in before_jobs]
    assert [dict(row) for row in after_events] == [dict(row) for row in before_events]
    scope = dict(rows(v7_database, "SELECT * FROM job_scopes")[0])
    assert scope == {
        "job_id": before_jobs[0]["id"],
        "scope_type": "document",
        "scope_id": str(before_jobs[0]["document_id"]),
        "scope_role": "anchor",
        "ordinal": 0,
        "created_at": scope["created_at"],
    }


def test_v8_upgrade_rolls_back_on_copy_failure(v7_database, monkeypatch):
    monkeypatch.setattr(
        migrations,
        "_copy_v8_jobs",
        lambda _connection: (_ for _ in ()).throw(RuntimeError("copy failed")),
    )
    with pytest.raises(RuntimeError, match="copy failed"):
        migrate(v7_database)
    assert v7_database.execute("PRAGMA user_version").fetchone()[0] == 7
    assert v7_database.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_v8_upgrade_passes_integrity_and_foreign_key_checks(v7_database):
    migrate(v7_database)
    assert v7_database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert v7_database.execute("PRAGMA foreign_key_check").fetchall() == []
