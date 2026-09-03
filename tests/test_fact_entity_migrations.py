import sqlite3

import pytest

from pkb.knowledge import migrations
from pkb.knowledge.migrations import migrate


KNOWLEDGE_TABLES = {
    "document_text_versions",
    "entities",
    "entity_aliases",
    "entity_mentions",
    "facts",
    "fact_evidence",
    "fact_relations",
    "fact_relation_evidence",
    "entity_merge_events",
    "knowledge_events",
    "derivation_scopes",
    "job_scopes",
}


def test_v8_retains_fact_entity_schema(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert KNOWLEDGE_TABLES <= tables
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 8


@pytest.fixture
def knowledge_connection(tmp_path):
    connection = sqlite3.connect(tmp_path / "constraints.db")
    migrate(connection)
    yield connection
    connection.close()


def insert_entity(connection, name, *, review_status="accepted"):
    return connection.execute(
        """INSERT INTO entities
           (entity_type, canonical_name, normalized_name, review_status)
           VALUES ('person', ?, ?, ?)""",
        (name, name.casefold(), review_status),
    ).lastrowid


def insert_fact(
    connection,
    *,
    subject_entity_id=None,
    object_entity_id=None,
    object_value_json='"value"',
    object_type="string",
    review_status="pending",
    knowledge_status=None,
    fact_key=None,
):
    subject_entity_id = subject_entity_id or insert_entity(connection, "subject")
    fact_key = fact_key or f"{subject_entity_id:064x}"
    return connection.execute(
        """INSERT INTO facts
           (fact_key, subject_entity_id, predicate, object_entity_id,
            object_value_json, object_type, object_normalized_text,
            review_status, knowledge_status)
           VALUES (?, ?, 'knows', ?, ?, ?, 'value', ?, ?)""",
        (
            fact_key,
            subject_entity_id,
            object_entity_id,
            object_value_json,
            object_type,
            review_status,
            knowledge_status,
        ),
    ).lastrowid


def insert_relation(connection, left, right, relation_type="conflicts_with"):
    return connection.execute(
        """INSERT INTO fact_relations
           (left_fact_id, right_fact_id, relation_type, explanation)
           VALUES (?, ?, ?, 'schema test')""",
        (left, right, relation_type),
    ).lastrowid


def test_fact_object_is_exactly_one_of_entity_or_value(knowledge_connection):
    entity = insert_entity(knowledge_connection, "Zhang San")
    with pytest.raises(sqlite3.IntegrityError):
        insert_fact(
            knowledge_connection,
            subject_entity_id=entity,
            object_entity_id=None,
            object_value_json=None,
            object_type="string",
        )


@pytest.mark.parametrize("review_status", ["pending", "rejected"])
def test_unreviewed_fact_cannot_have_knowledge_status(
    knowledge_connection, review_status
):
    with pytest.raises(sqlite3.IntegrityError):
        insert_fact(
            knowledge_connection,
            review_status=review_status,
            knowledge_status="active",
        )


def test_symmetric_relation_requires_canonical_order(knowledge_connection):
    left = insert_fact(knowledge_connection, fact_key="1" * 64)
    right = insert_fact(knowledge_connection, fact_key="2" * 64)
    with pytest.raises(sqlite3.IntegrityError):
        insert_relation(knowledge_connection, right, left)


def test_fact_publish_requires_accepted_entities_and_valid_evidence(
    knowledge_connection,
):
    pending_subject = insert_entity(
        knowledge_connection, "Pending Subject", review_status="pending"
    )
    fact_id = insert_fact(
        knowledge_connection,
        subject_entity_id=pending_subject,
        fact_key="3" * 64,
    )
    with pytest.raises(sqlite3.IntegrityError, match="subject_entity_not_accepted"):
        knowledge_connection.execute(
            """UPDATE facts SET review_status='accepted', knowledge_status='active'
               WHERE id=?""",
            (fact_id,),
        )


def test_historical_fact_and_audit_event_cannot_be_deleted(knowledge_connection):
    fact_id = insert_fact(knowledge_connection, fact_key="4" * 64)
    event_id = knowledge_connection.execute(
        """INSERT INTO knowledge_events
           (object_type, object_id, event_type, actor_type, reason)
           VALUES ('fact', ?, 'created', 'system', 'schema test')""",
        (fact_id,),
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="historical_facts"):
        knowledge_connection.execute("DELETE FROM facts WHERE id=?", (fact_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append_only"):
        knowledge_connection.execute(
            "DELETE FROM knowledge_events WHERE id=?", (event_id,)
        )


def test_accepted_fact_cannot_be_inserted_without_valid_evidence(
    knowledge_connection,
):
    with pytest.raises(sqlite3.IntegrityError, match="fact_has_no_valid_evidence"):
        insert_fact(
            knowledge_connection,
            review_status="accepted",
            knowledge_status="active",
            fact_key="5" * 64,
        )


def test_v6_to_v8_preserves_existing_rows_and_starts_knowledge_tables_empty(
    tmp_path,
):
    connection = sqlite3.connect(tmp_path / "upgrade.db")
    connection.execute("PRAGMA foreign_keys = ON")
    with connection:
        for version in range(1, 7):
            for statement in migrations._MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {version}")
        connection.execute(
            """INSERT INTO documents
               (id, identity_key, title, plain_content, source_content_hash,
                normalized_content_hash, normalization_version, schema_version)
               VALUES (1, 'legacy-document', 'Legacy', 'body', 'source-v1',
                       'normalized-v1', 1, 1)"""
        )
        connection.execute(
            """INSERT INTO documents
               (id, identity_key, title, plain_content, source_content_hash,
                normalized_content_hash, normalization_version, schema_version)
               VALUES (2, 'legacy-target', 'Target', 'target body', 'source-v2',
                       'normalized-v2', 1, 1)"""
        )
        connection.execute(
            """INSERT INTO derivations
               (id, document_id, kind, payload_json, input_hash,
                source_content_hash, normalized_content_hash,
                normalization_version, schema_version, prompt_version, status)
               VALUES ('derivation-1', 1, 'article', '{"summary":"legacy"}',
                       'input-v1', 'source-v1', 'normalized-v1', 1, 1,
                       'prompt-v1', 'completed')"""
        )
        connection.execute(
            """INSERT INTO jobs
               (id, job_type, document_id, input_hash, pipeline_version, status)
               VALUES (1, 'derive', 1, 'input-v1', 'pipeline-v1', 'succeeded')"""
        )
        connection.execute(
            """INSERT INTO relations
               (id, source_document_id, target_document_id, relation_type,
                evidence, derivation_id, candidate_evidence_json, explanation)
               VALUES (1, 1, 2, 'supports', 'legacy evidence', 'derivation-1',
                       '[]', 'legacy explanation')"""
        )

    legacy_tables = ("documents", "derivations", "relations")
    before = {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        for table in legacy_tables
    }

    migrate(connection)

    after = {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        for table in legacy_tables
    }
    assert after == before
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
    assert connection.execute(
        """SELECT id, job_type, document_id, input_hash, pipeline_version, status
           FROM jobs"""
    ).fetchone() == (1, "derive", 1, "input-v1", "pipeline-v1", "succeeded")
    assert connection.execute("SELECT count(*) FROM job_scopes").fetchone()[0] == 1
    for table in KNOWLEDGE_TABLES - {"job_scopes"}:
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
