"""Versioned SQLite schema and safe database rebuild primitives."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path


SCHEMA_VERSION = 1


_MIGRATION_1 = (
    """CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY,
        identity_key TEXT NOT NULL UNIQUE,
        source_type TEXT,
        title TEXT,
        author TEXT,
        plain_content TEXT,
        canonical_url TEXT,
        source_created_at TEXT,
        first_saved_at TEXT,
        last_seen_at TEXT,
        source_content_hash TEXT NOT NULL,
        normalized_content_hash TEXT NOT NULL,
        normalization_version INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS source_memberships (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        source_item_id TEXT NOT NULL,
        collection_id TEXT NOT NULL DEFAULT '',
        collection_title TEXT,
        raw_path TEXT,
        raw_line INTEGER,
        source_url TEXT,
        observed_at TEXT,
        UNIQUE(source, source_item_id, collection_id)
    )""",
    """CREATE TABLE IF NOT EXISTS document_url_aliases (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        url TEXT NOT NULL UNIQUE,
        observed_url TEXT,
        source TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS document_id_aliases (
        alias_document_id INTEGER PRIMARY KEY,
        canonical_document_id INTEGER NOT NULL REFERENCES documents(id),
        merge_id INTEGER REFERENCES document_merges(id),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        remote_url TEXT NOT NULL,
        local_path TEXT,
        media_type TEXT,
        byte_size INTEGER,
        checksum TEXT,
        triage_bucket TEXT,
        triage_reasons_json TEXT,
        source_locator TEXT,
        media_order INTEGER NOT NULL DEFAULT 0,
        UNIQUE(document_id, remote_url)
    )""",
    """CREATE TABLE IF NOT EXISTS derivations (
        id TEXT PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        source_content_hash TEXT NOT NULL,
        normalized_content_hash TEXT NOT NULL,
        normalization_version INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        prompt_version TEXT NOT NULL,
        provider TEXT,
        model TEXT,
        generation_parameters_json TEXT,
        status TEXT NOT NULL,
        supersedes_derivation_id TEXT REFERENCES derivations(id),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(document_id, kind, input_hash, prompt_version)
    )""",
    """CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY,
        normalized_name TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS document_tags (
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        origin TEXT NOT NULL,
        confidence REAL,
        derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(document_id, tag_id, origin)
    )""",
    """CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY,
        normalized_name TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        parent_id INTEGER REFERENCES topics(id) ON DELETE SET NULL
    )""",
    """CREATE TABLE IF NOT EXISTS document_topics (
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
        origin TEXT NOT NULL,
        confidence REAL,
        derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(document_id, topic_id, origin)
    )""",
    """CREATE TABLE IF NOT EXISTS relations (
        id INTEGER PRIMARY KEY,
        source_document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        target_document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        relation_type TEXT NOT NULL,
        score REAL,
        evidence TEXT NOT NULL,
        derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(source_document_id <> target_document_id),
        UNIQUE(source_document_id, target_document_id, relation_type)
    )""",
    """CREATE TABLE IF NOT EXISTS reading_state (
        document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'unread',
        manual_priority INTEGER,
        priority_reason TEXT,
        last_reviewed_at TEXT,
        user_note_ref TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY,
        job_type TEXT NOT NULL,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        input_hash TEXT NOT NULL,
        pipeline_version TEXT NOT NULL,
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        worker_id TEXT,
        leased_at TEXT,
        lease_expires_at TEXT,
        heartbeat_at TEXT,
        error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(job_type, document_id, input_hash, pipeline_version)
    )""",
    """CREATE TABLE IF NOT EXISTS job_events (
        id INTEGER PRIMARY KEY,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        worker_id TEXT,
        details_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS document_merges (
        id INTEGER PRIMARY KEY,
        survivor_document_id INTEGER NOT NULL REFERENCES documents(id),
        duplicate_document_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        reading_state_policy TEXT NOT NULL DEFAULT 'reject',
        metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(survivor_document_id <> duplicate_document_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_memberships_document ON source_memberships(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_url_aliases_document ON document_url_aliases(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_id_aliases_canonical ON document_id_aliases(canonical_document_id)",
    "CREATE INDEX IF NOT EXISTS idx_id_aliases_merge ON document_id_aliases(merge_id)",
    "CREATE INDEX IF NOT EXISTS idx_media_document ON media(document_id, media_order)",
    "CREATE INDEX IF NOT EXISTS idx_derivations_document_kind ON derivations(document_id, kind, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_derivations_supersedes ON derivations(supersedes_derivation_id)",
    "CREATE INDEX IF NOT EXISTS idx_document_tags_tag ON document_tags(tag_id)",
    "CREATE INDEX IF NOT EXISTS idx_document_tags_derivation ON document_tags(derivation_id)",
    "CREATE INDEX IF NOT EXISTS idx_topics_parent ON topics(parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_document_topics_topic ON document_topics(topic_id)",
    "CREATE INDEX IF NOT EXISTS idx_document_topics_derivation ON document_topics(derivation_id)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, lease_expires_at, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_document ON jobs(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_document_id, relation_type)",
    "CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_document_id, relation_type)",
    "CREATE INDEX IF NOT EXISTS idx_relations_derivation ON relations(derivation_id)",
    "CREATE INDEX IF NOT EXISTS idx_reading_state_document ON reading_state(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_merges_survivor ON document_merges(survivor_document_id)",
)


def migrate(connection: sqlite3.Connection) -> None:
    """Migrate *connection* to the latest supported schema version."""
    connection.execute("PRAGMA foreign_keys = ON")
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if current_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {current_version} is newer than supported "
            f"version {SCHEMA_VERSION}"
        )
    if current_version == SCHEMA_VERSION:
        return

    with connection:
        for statement in _MIGRATION_1:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def rebuild_database(
    target: str | Path,
    builder: Callable[[sqlite3.Connection], None],
) -> None:
    """Build a sibling temporary database and atomically replace *target*."""
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path.with_suffix(target_path.suffix + ".tmp")
    temporary_path.unlink(missing_ok=True)

    connection: sqlite3.Connection | None = None
    replaced = False
    try:
        connection = sqlite3.connect(temporary_path)
        migrate(connection)
        with connection:
            builder(connection)
        connection.close()
        connection = sqlite3.connect(temporary_path)
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            detail = "no result" if result is None else result[0]
            raise RuntimeError(f"temporary database failed integrity check: {detail}")
        connection.close()
        connection = None
        temporary_path.replace(target_path)
        replaced = True
    finally:
        if connection is not None:
            connection.close()
        if not replaced:
            temporary_path.unlink(missing_ok=True)
