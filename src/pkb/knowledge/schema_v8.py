"""SQLite v8 cross-domain job schema primitives."""

from __future__ import annotations


CREATE_V8_JOB_TABLES: tuple[str, ...] = (
    """CREATE TABLE jobs_v8 (
        id INTEGER PRIMARY KEY,
        job_type TEXT NOT NULL CHECK(length(trim(job_type)) > 0),
        document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
        scope_hash TEXT NOT NULL CHECK(length(scope_hash) = 64),
        input_hash TEXT NOT NULL CHECK(length(input_hash) > 0),
        pipeline_version TEXT NOT NULL CHECK(length(trim(pipeline_version)) > 0),
        status TEXT NOT NULL CHECK(status IN (
            'pending','running','succeeded','failed','dead-letter'
        )),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        worker_id TEXT,
        leased_at TEXT,
        lease_expires_at TEXT,
        heartbeat_at TEXT,
        error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(job_type, scope_hash, input_hash, pipeline_version),
        CHECK(
            (status = 'running' AND worker_id IS NOT NULL AND leased_at IS NOT NULL
             AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL)
            OR
            (status <> 'running' AND worker_id IS NULL AND leased_at IS NULL
             AND lease_expires_at IS NULL AND heartbeat_at IS NULL)
        )
    )""",
    """CREATE TABLE job_events_v8 (
        id INTEGER PRIMARY KEY,
        job_id INTEGER NOT NULL REFERENCES jobs_v8(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        worker_id TEXT,
        details_json TEXT CHECK(details_json IS NULL OR json_valid(details_json)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE job_scopes_v8 (
        job_id INTEGER NOT NULL REFERENCES jobs_v8(id) ON DELETE CASCADE,
        scope_type TEXT NOT NULL CHECK(scope_type IN (
            'document','entity','fact','fact_relation','synthesis'
        )),
        scope_id TEXT NOT NULL CHECK(length(scope_id) > 0),
        scope_role TEXT NOT NULL CHECK(scope_role IN (
            'anchor','input','output','context'
        )),
        ordinal INTEGER NOT NULL DEFAULT 0 CHECK(ordinal >= 0),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(job_id, scope_type, scope_id, scope_role),
        UNIQUE(job_id, scope_role, ordinal)
    )""",
)


FINALIZE_V8_JOB_TABLES: tuple[str, ...] = (
    "DROP TABLE job_scopes",
    "DROP TABLE job_events",
    "DROP TABLE jobs",
    "ALTER TABLE jobs_v8 RENAME TO jobs",
    "ALTER TABLE job_events_v8 RENAME TO job_events",
    "ALTER TABLE job_scopes_v8 RENAME TO job_scopes",
    "CREATE INDEX idx_jobs_claim ON jobs(status, lease_expires_at, created_at)",
    "CREATE INDEX idx_jobs_document ON jobs(document_id)",
    "CREATE INDEX idx_jobs_scope_hash ON jobs(scope_hash)",
    "CREATE INDEX idx_job_events_job ON job_events(job_id, created_at)",
    """CREATE INDEX idx_job_scopes_lookup
       ON job_scopes(scope_type, scope_id, scope_role, job_id)""",
    """CREATE UNIQUE INDEX uq_job_anchor
       ON job_scopes(job_id) WHERE scope_role='anchor'""",
)


# Kept as a public description of the complete SQL shape.  Migration v8 is a
# Python callable because populated queues require per-row canonical hashes.
KNOWLEDGE_SCHEMA_V8 = CREATE_V8_JOB_TABLES + FINALIZE_V8_JOB_TABLES
