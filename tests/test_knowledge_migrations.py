import sqlite3

import pytest

from pkb.knowledge.migrations import migrate, rebuild_database


CORE_TABLES = {
    "documents",
    "source_memberships",
    "document_url_aliases",
    "document_id_aliases",
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
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migration_is_idempotent(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")

    migrate(connection)
    migrate(connection)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_migration_rejects_database_from_a_newer_schema_version(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    connection.execute("PRAGMA user_version = 2")

    with pytest.raises(RuntimeError, match="newer than supported"):
        migrate(connection)


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
           VALUES ('doc-1', 'identity-1', 'source-hash', 'normalized-hash', 1, 1)"""
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO documents
               (id, identity_key, source_content_hash, normalized_content_hash,
                normalization_version, schema_version)
               VALUES ('doc-2', 'identity-1', 'other-source', 'other-normalized', 1, 1)"""
        )

    connection.execute(
        """INSERT INTO source_memberships
           (document_id, source, source_item_id, collection_id)
           VALUES ('doc-1', 'zhihu', 'answer-1', 'favorites-1')"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO source_memberships
               (document_id, source, source_item_id, collection_id)
               VALUES ('doc-1', 'zhihu', 'answer-1', 'favorites-1')"""
        )

    connection.execute(
        "INSERT INTO document_url_aliases (document_id, url) VALUES ('doc-1', 'https://example.test/1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO document_url_aliases (document_id, url) VALUES ('doc-1', 'https://example.test/1')"
        )

    connection.execute(
        """INSERT INTO jobs
           (job_type, document_id, input_hash, pipeline_version, status)
           VALUES ('derive', 'doc-1', 'input-1', 'v1', 'pending')"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO jobs
               (job_type, document_id, input_hash, pipeline_version, status)
               VALUES ('derive', 'doc-1', 'input-1', 'v1', 'pending')"""
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
               VALUES ('doc-1', 'identity-1', 'source-hash', 'normalized-hash', 1, 1)"""
        )

    rebuild_database(target, builder)

    connection = sqlite3.connect(target)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("SELECT id FROM documents").fetchone()[0] == "doc-1"
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_rebuild_preserves_target_and_cleans_temporary_file_when_builder_fails(tmp_path):
    target = tmp_path / "knowledge.db"
    original = b"existing database bytes"
    target.write_bytes(original)

    def builder(connection):
        connection.execute(
            """INSERT INTO documents
               (id, identity_key, source_content_hash, normalized_content_hash,
                normalization_version, schema_version)
               VALUES ('doc-1', 'identity-1', 'source-hash', 'normalized-hash', 1, 1)"""
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
