"""Safe database workflows used by derivation interfaces."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pkb.derive.jobs import JobQueue
from pkb.derive.prompts import ARTICLE_PROMPT_VERSION
from pkb.knowledge.fingerprint import derivation_input_hash


@dataclass(frozen=True)
class DerivationScope:
    documents: int
    estimated_input_chars: int


def _readonly(database: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{Path(database).resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def article_scope(database: str | Path, limit: int | None) -> DerivationScope:
    suffix = "" if limit is None else " LIMIT ?"
    parameters = () if limit is None else (limit,)
    with _readonly(database) as connection:
        row = connection.execute(
            """SELECT count(*) AS documents, coalesce(sum(length(plain_content)), 0) AS chars
               FROM (SELECT d.plain_content FROM jobs j JOIN documents d ON d.id=j.document_id
                     WHERE j.job_type='article' AND j.status='pending'
                     ORDER BY j.created_at, j.id""" + suffix + ")",
            parameters,
        ).fetchone()
    return DerivationScope(int(row["documents"]), int(row["chars"]))


def job_status(database: str | Path) -> dict[str, int]:
    result = {name: 0 for name in ("pending", "running", "failed", "succeeded", "dead-letter")}
    with _readonly(database) as connection:
        for row in connection.execute(
            "SELECT status, count(*) AS count FROM jobs WHERE job_type='article' GROUP BY status"
        ):
            result[row["status"]] = int(row["count"])
    return result


def retry_failed(database: str | Path, limit: int) -> int:
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        ids = [row[0] for row in connection.execute(
            "SELECT id FROM jobs WHERE job_type='article' AND status='failed' ORDER BY updated_at, id LIMIT ?",
            (limit,),
        )]
        for job_id in ids:
            connection.execute(
                """UPDATE jobs SET status='pending', attempts=0, worker_id=NULL, leased_at=NULL,
                   lease_expires_at=NULL, heartbeat_at=NULL, error=NULL,
                   updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='failed'""",
                (job_id,),
            )
            connection.execute(
                "INSERT INTO job_events(job_id, event_type, details_json) VALUES (?, 'retried', ?)",
                (job_id, json.dumps({"from_status": "failed"}, sort_keys=True)),
            )
        connection.commit()
        return len(ids)
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def migration_candidates(
    database: str | Path, normalization_version: int, limit: int | None
) -> list[tuple[int, str]]:
    parameters: tuple[object, ...] = (normalization_version, normalization_version)
    with _readonly(database) as connection:
        rows = connection.execute(
            """SELECT d.id, d.source_content_hash, d.normalized_content_hash,
                      d.normalization_version
               FROM documents d
               WHERE d.normalization_version=?
                 AND EXISTS (SELECT 1 FROM derivations x WHERE x.document_id=d.id
                             AND x.kind='article' AND x.status='accepted'
                             AND x.normalization_version<>?)
               ORDER BY d.id""",
            parameters,
        ).fetchall()
        candidates: list[tuple[int, str]] = []
        for row in rows:
            document_id = int(row["id"])
            input_hash = derivation_input_hash(
                row["source_content_hash"], row["normalized_content_hash"],
                int(row["normalization_version"]),
            )
            already_exists = connection.execute(
                """SELECT 1 FROM jobs WHERE job_type='article' AND document_id=?
                   AND input_hash=? AND pipeline_version=?
                   UNION ALL
                   SELECT 1 FROM derivations WHERE document_id=? AND kind='article'
                   AND input_hash=? AND prompt_version=? AND status='accepted'
                   LIMIT 1""",
                (document_id, input_hash, ARTICLE_PROMPT_VERSION,
                 document_id, input_hash, ARTICLE_PROMPT_VERSION),
            ).fetchone()
            if already_exists is None:
                candidates.append((document_id, input_hash))
                if limit is not None and len(candidates) >= limit:
                    break
    return candidates


def queue_migration(database: str | Path, normalization_version: int, limit: int | None) -> int:
    candidates = migration_candidates(database, normalization_version, limit)
    with JobQueue(database) as queue:
        for document_id, input_hash in candidates:
            queue.enqueue("article", document_id, input_hash, ARTICLE_PROMPT_VERSION)
    return len(candidates)
