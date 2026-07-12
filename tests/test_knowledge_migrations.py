import sqlite3

import pytest

from pkb.knowledge.migrations import migrate, rebuild_database


CORE_TABLES = {
    "documents",
    "source_memberships",
    "document_url_aliases",
    "document_id_aliases",
    "document_identity_aliases",
    "media",
    "derivations",
    "tags",
    "document_tags",
    "topics",
    "document_topics",
    "relations",
    "reading_state",
    "jobs",
    "job_events",
    "document_merges",
    "documents_search_content",
    "documents_fts",
}


def test_migration_creates_versioned_core_schema(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")

    migrate(connection)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert CORE_TABLES <= tables
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migration_is_idempotent(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")

    migrate(connection)
    migrate(connection)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_migration_rejects_database_from_a_newer_schema_version(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    connection.execute("PRAGMA user_version = 4")

    with pytest.raises(RuntimeError, match="newer than supported"):
        migrate(connection)


def test_document_schema_matches_repository_contract(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)

    document_columns = {
        row[1]: row[2] for row in connection.execute("PRAGMA table_info(documents)")
    }
    assert document_columns["id"] == "INTEGER"
    assert "plain_content" in document_columns
    assert "content" not in document_columns

    document_reference_columns = {
        "source_memberships": "document_id",
        "document_url_aliases": "document_id",
        "media": "document_id",
        "derivations": "document_id",
        "document_tags": "document_id",
        "document_topics": "document_id",
        "reading_state": "document_id",
        "jobs": "document_id",
        "document_id_aliases": "canonical_document_id",
        "document_identity_aliases": "canonical_document_id",
        "document_merges": "survivor_document_id",
    }
    for table, column in document_reference_columns.items():
        columns = {
            row[1]: row[2] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        assert columns[column] == "INTEGER", f"{table}.{column}"

    relation_columns = {
        row[1]: row[2] for row in connection.execute("PRAGMA table_info(relations)")
    }
    assert relation_columns["source_document_id"] == "INTEGER"
    assert relation_columns["target_document_id"] == "INTEGER"

    merge_columns = {
        row[1]: row[2]
        for row in connection.execute("PRAGMA table_info(document_merges)")
    }
    assert merge_columns["duplicate_document_id"] == "INTEGER"


def test_document_id_alias_records_the_merge_that_created_it(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)

    foreign_keys = {
        (row[3], row[2], row[4])
        for row in connection.execute("PRAGMA foreign_key_list(document_id_aliases)")
    }
    assert ("merge_id", "document_merges", "id") in foreign_keys


def test_document_ids_are_not_reused_after_deletion(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)
    for identity in ("identity-1", "identity-2"):
        connection.execute(
            """INSERT INTO documents
               (identity_key, source_content_hash, normalized_content_hash,
                normalization_version, schema_version)
               VALUES (?, 'source-hash', 'normalized-hash', 1, 1)""",
            (identity,),
        )
    assert connection.execute("SELECT max(id) FROM documents").fetchone()[0] == 2

    connection.execute("DELETE FROM documents WHERE id = 2")
    cursor = connection.execute(
        """INSERT INTO documents
           (identity_key, source_content_hash, normalized_content_hash,
            normalization_version, schema_version)
           VALUES ('identity-3', 'source-hash', 'normalized-hash', 1, 1)"""
    )

    assert cursor.lastrowid == 3


def test_document_identity_alias_references_document_and_merge(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)

    columns = {
        row[1]: row[2]
        for row in connection.execute("PRAGMA table_info(document_identity_aliases)")
    }
    assert columns["alias_identity_key"] == "TEXT"
    assert columns["canonical_document_id"] == "INTEGER"
    assert columns["merge_id"] == "INTEGER"
    foreign_keys = {
        (row[3], row[2], row[4])
        for row in connection.execute(
            "PRAGMA foreign_key_list(document_identity_aliases)"
        )
    }
    assert ("canonical_document_id", "documents", "id") in foreign_keys
    assert ("merge_id", "document_merges", "id") in foreign_keys


def test_every_foreign_key_column_has_a_leading_index(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    missing = []
    for table in tables:
        indexed_leading_columns = {
            connection.execute(f'PRAGMA index_info("{index[1]}")').fetchone()[2]
            for index in connection.execute(f'PRAGMA index_list("{table}")')
        }
        for foreign_key in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
            if foreign_key[3] not in indexed_leading_columns:
                missing.append(f"{table}.{foreign_key[3]}")

    assert missing == []


def test_schema_enforces_identity_membership_alias_and_job_uniqueness(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)
    connection.execute(
        """INSERT INTO documents
           (id, identity_key, source_content_hash, normalized_content_hash,
            normalization_version, schema_version)
           VALUES (1, 'identity-1', 'source-hash', 'normalized-hash', 1, 1)"""
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO documents
               (id, identity_key, source_content_hash, normalized_content_hash,
                normalization_version, schema_version)
               VALUES (2, 'identity-1', 'other-source', 'other-normalized', 1, 1)"""
        )

    connection.execute(
        """INSERT INTO source_memberships
           (document_id, source, source_item_id, collection_id)
           VALUES (1, 'zhihu', 'answer-1', 'favorites-1')"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO source_memberships
               (document_id, source, source_item_id, collection_id)
               VALUES (1, 'zhihu', 'answer-1', 'favorites-1')"""
        )

    connection.execute(
        "INSERT INTO document_url_aliases (document_id, url) VALUES (1, 'https://example.test/1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO document_url_aliases (document_id, url) VALUES (1, 'https://example.test/1')"
        )

    connection.execute(
        """INSERT INTO jobs
           (job_type, document_id, input_hash, pipeline_version, status)
           VALUES ('derive', 1, 'input-1', 'v1', 'pending')"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO jobs
               (job_type, document_id, input_hash, pipeline_version, status)
               VALUES ('derive', 1, 'input-1', 'v1', 'pending')"""
        )


def test_schema_enforces_foreign_keys(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO source_memberships
               (document_id, source, source_item_id, collection_id)
               VALUES ('missing', 'zhihu', 'answer-1', 'favorites-1')"""
        )


def test_rebuild_replaces_target_only_after_successful_integrity_check(tmp_path):
    target = tmp_path / "knowledge.db"
    target.write_bytes(b"old database")

    def builder(connection):
        connection.execute(
            """INSERT INTO documents
               (id, identity_key, source_content_hash, normalized_content_hash,
                normalization_version, schema_version)
               VALUES (1, 'identity-1', 'source-hash', 'normalized-hash', 1, 1)"""
        )

    rebuild_database(target, builder)

    connection = sqlite3.connect(target)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("SELECT id FROM documents").fetchone()[0] == 1
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_v1_to_v2_migration_creates_empty_external_content_search_projection(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    connection.execute("PRAGMA foreign_keys = ON")
    from pkb.knowledge.migrations import _MIGRATION_1

    with connection:
        for statement in _MIGRATION_1:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            """INSERT INTO documents
               (identity_key, title, plain_content, source_content_hash,
                normalized_content_hash, normalization_version, schema_version)
               VALUES ('legacy', '旧标题', '旧正文', 's', 'n', 1, 1)"""
        )

    migrate(connection)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    assert connection.execute("SELECT count(*) FROM documents_search_content").fetchone()[0] == 0
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='documents_fts'"
    ).fetchone()[0]
    assert "content='documents_search_content'" in sql
    columns = {row[1] for row in connection.execute("PRAGMA table_info(documents_search_content)")}
    assert {
        "document_id", "title", "content", "summary", "tags",
        "title_terms", "content_terms", "summary_terms", "tags_terms",
        "strategy", "tokenizer_version", "dictionary_fingerprint",
    } <= columns


def test_search_projection_triggers_follow_insert_update_and_delete(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)
    connection.execute(
        """INSERT INTO documents
           (id, identity_key, source_content_hash, normalized_content_hash,
            normalization_version, schema_version)
           VALUES (1, 'identity', 's', 'n', 1, 1)"""
    )
    connection.execute(
        """INSERT INTO documents_search_content
           (document_id, title, content, title_terms, content_terms,
            strategy, tokenizer_version, dictionary_fingerprint)
           VALUES (1, '甲', '乙', '甲', '乙', 'jieba', '1', 'dict')"""
    )
    assert connection.execute(
        "SELECT title_terms FROM documents_fts WHERE rowid=1"
    ).fetchone()[0] == "甲"

    connection.execute(
        "UPDATE documents_search_content SET title_terms='新词' WHERE document_id=1"
    )
    assert connection.execute(
        "SELECT title_terms FROM documents_fts WHERE rowid=1"
    ).fetchone()[0] == "新词"

    connection.execute("DELETE FROM documents_search_content WHERE document_id=1")
    assert connection.execute(
        "SELECT count(*) FROM documents_fts WHERE rowid=1"
    ).fetchone()[0] == 0


def test_rebuild_preserves_target_and_cleans_temporary_file_when_builder_fails(tmp_path):
    target = tmp_path / "knowledge.db"
    original = b"existing database bytes"
    target.write_bytes(original)

    def builder(connection):
        connection.execute(
            """INSERT INTO documents
               (id, identity_key, source_content_hash, normalized_content_hash,
                normalization_version, schema_version)
               VALUES (1, 'identity-1', 'source-hash', 'normalized-hash', 1, 1)"""
        )
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError, match="build failed"):
        rebuild_database(target, builder)

    assert target.read_bytes() == original
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_rebuild_preserves_target_when_temporary_database_is_corrupt(tmp_path):
    target = tmp_path / "knowledge.db"
    original = b"existing database bytes"
    target.write_bytes(original)

    def builder(connection):
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_master SET sql = 'CREATE TABLE documents (' WHERE name = 'documents'"
        )
        connection.execute("PRAGMA writable_schema = OFF")

    with pytest.raises(sqlite3.DatabaseError):
        rebuild_database(target, builder)

    assert target.read_bytes() == original
    assert not target.with_suffix(target.suffix + ".tmp").exists()
