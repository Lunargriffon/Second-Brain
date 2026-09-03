"""Bounded, recoverable article-derivation pipeline."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pkb.derive.jobs import Job, JobQueue
from pkb.derive.models import DerivationValidationError, validate_article_derivation
from pkb.derive.prompts import ARTICLE_PROMPT_VERSION, ARTICLE_SYSTEM_PROMPT
from pkb.derive.provider import (
    DerivationProvider,
    ProviderAuthError,
    ProviderInvalidRequestError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from pkb.knowledge.search import SearchIndex


ARTICLE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PipelineResult:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    stopped: bool = False


class DerivationPipeline:
    """Process a fixed number of article jobs; never runs an unbounded batch."""

    def __init__(
        self,
        database: str | Path,
        provider: DerivationProvider,
        *,
        run_log: str | Path,
        worker_id: str | None = None,
        retry_delays: tuple[float, ...] = (1.0, 4.0),
        sleeper: Callable[[float], None] = time.sleep,
        stage_hook: Callable[[str], None] | None = None,
    ) -> None:
        if any(delay < 0 for delay in retry_delays):
            raise ValueError("retry delays cannot be negative")
        self.database = Path(database)
        self.provider = provider
        self.run_log = Path(run_log)
        self.worker_id = worker_id or f"pipeline-{uuid.uuid4().hex}"
        self.retry_delays = retry_delays
        self.sleeper = sleeper
        self.stage_hook = stage_hook or (lambda _stage: None)

    def run(self, *, limit: int = 10) -> PipelineResult:
        if limit <= 0:
            raise ValueError("limit must be positive")
        processed = succeeded = failed = 0
        stopped = False
        with JobQueue(self.database) as queue:
            search = SearchIndex(queue)
            for _ in range(limit):
                job = queue.claim(self.worker_id, job_type="article")
                if job is None:
                    break
                processed += 1
                try:
                    if self._accepted(queue.connection, job):
                        self._restore_projection_and_succeed(queue, search, job)
                        succeeded += 1
                        continue
                    document = queue.connection.execute(
                        "SELECT * FROM documents WHERE id=?", (job.document_id,)
                    ).fetchone()
                    if document is None:
                        queue.fail(job.id, self.worker_id, "document_missing")
                        failed += 1
                        continue
                    response = self._complete_with_retry(queue, job, document["plain_content"] or "")
                    # A slow provider call may outlive its lease.  Re-check ownership
                    # before any untrusted result can be promoted.
                    queue.heartbeat(job.id, self.worker_id)
                    derived = validate_article_derivation(
                        response, source_text=document["plain_content"] or ""
                    )
                    payload = asdict(derived)
                    derivation_id = self._derivation_id(job)
                    self._append_log(job, derivation_id, "validated")
                    def publish(connection: sqlite3.Connection) -> None:
                        self._insert_derivation(
                            connection, job, document, derivation_id, payload
                        )
                        self.stage_hook("derivation")
                        self._project(
                            connection, search, job.document_id, derivation_id, payload
                        )
                        self.stage_hook("projection")

                    queue.publish_and_succeed(job.id, self.worker_id, publish)
                    succeeded += 1
                except (DerivationValidationError, ProviderMalformedResponseError):
                    queue.fail(job.id, self.worker_id, "invalid_derivation")
                    self._append_log(job, None, "rejected", "invalid_derivation")
                    failed += 1
                except (ProviderAuthError, ProviderInvalidRequestError):
                    queue.fail(job.id, self.worker_id, "provider_permanent")
                    self._append_log(job, None, "failed", "provider_permanent")
                    failed += 1
                    stopped = True
                    break
                except (ProviderRateLimitError, ProviderTemporaryError):
                    queue.fail(job.id, self.worker_id, "provider_temporary")
                    self._append_log(job, None, "failed", "provider_temporary")
                    failed += 1
                    stopped = True
                    break
        return PipelineResult(processed, succeeded, failed, stopped)

    def _complete_with_retry(self, queue: JobQueue, job: Job, source_text: str):
        user = f"请根据以下原文生成严格 JSON。\n\n原文：\n{source_text}"
        for attempt in range(len(self.retry_delays) + 1):
            queue.heartbeat(job.id, self.worker_id)
            try:
                return self.provider.complete(system=ARTICLE_SYSTEM_PROMPT, user=user)
            except (ProviderRateLimitError, ProviderTemporaryError):
                if attempt == len(self.retry_delays):
                    raise
                self.sleeper(self.retry_delays[attempt])
        raise AssertionError("unreachable")

    @staticmethod
    def _derivation_id(job: Job) -> str:
        value = f"{job.document_id}\0article\0{job.input_hash}\0{ARTICLE_PROMPT_VERSION}"
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _accepted(connection: sqlite3.Connection, job: Job) -> bool:
        return connection.execute(
            """SELECT 1 FROM derivations WHERE document_id=? AND kind='article'
               AND input_hash=? AND prompt_version=? AND status='accepted'""",
            (job.document_id, job.input_hash, ARTICLE_PROMPT_VERSION),
        ).fetchone() is not None

    def _insert_derivation(self, connection, job, document, derivation_id, payload) -> None:
        connection.execute(
            """INSERT OR IGNORE INTO derivations
                   (id, document_id, kind, payload_json, input_hash, source_content_hash,
                    normalized_content_hash, normalization_version, schema_version,
                    prompt_version, provider, model, generation_parameters_json, status)
                   VALUES (?, ?, 'article', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted')""",
            (
                derivation_id, job.document_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True), job.input_hash,
                document["source_content_hash"], document["normalized_content_hash"],
                document["normalization_version"], ARTICLE_SCHEMA_VERSION,
                ARTICLE_PROMPT_VERSION, self.provider.provider_name,
                self.provider.model_name, "{}",
            ),
        )

    @staticmethod
    def _project(
        connection: sqlite3.Connection,
        search: SearchIndex,
        document_id: int,
        derivation_id: str,
        payload,
    ) -> None:
        connection.execute(
            "DELETE FROM document_tags WHERE document_id=? AND origin='ai'", (document_id,)
        )
        connection.execute(
            "DELETE FROM document_topics WHERE document_id=? AND origin='ai'", (document_id,)
        )
        for kind, table, join_table, values in (
            ("tag", "tags", "document_tags", payload["tags"]),
            ("topic", "topics", "document_topics", payload["topics"]),
        ):
            id_column = f"{kind}_id"
            for label in values:
                normalized = label["name"].strip().casefold()
                connection.execute(
                    f"INSERT OR IGNORE INTO {table}(normalized_name, display_name) VALUES (?, ?)",
                    (normalized, label["name"].strip()),
                )
                label_id = connection.execute(
                    f"SELECT id FROM {table} WHERE normalized_name=?", (normalized,)
                ).fetchone()[0]
                connection.execute(
                    f"""INSERT OR REPLACE INTO {join_table}
                            (document_id, {id_column}, origin, confidence, derivation_id)
                            VALUES (?, ?, 'ai', ?, ?)""",
                    (document_id, label_id, label["confidence"], derivation_id),
                )
        search.update_derived_projection(
            document_id, summary=payload["summary"],
            tags=tuple(item["name"] for item in payload["tags"]),
            commit=False,
        )

    def _restore_projection_and_succeed(self, queue: JobQueue, search: SearchIndex, job: Job) -> None:
        row = queue.connection.execute(
            """SELECT id, payload_json FROM derivations WHERE document_id=? AND kind='article'
               AND input_hash=? AND prompt_version=? AND status='accepted'""",
            (job.document_id, job.input_hash, ARTICLE_PROMPT_VERSION),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        queue.publish_and_succeed(
            job.id,
            self.worker_id,
            lambda connection: self._project(
                connection, search, job.document_id, row["id"], payload
            ),
        )

    def _append_log(
        self, job: Job, derivation_id: str | None, status: str, error_code: str | None = None
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "job_id": job.id,
            "document_id": job.document_id,
            "input_hash": job.input_hash,
            "prompt_version": ARTICLE_PROMPT_VERSION,
            "provider": self.provider.provider_name,
            "model": self.provider.model_name,
            "derivation_id": derivation_id,
            "status": status,
            "error_code": error_code,
        }
        self.run_log.parent.mkdir(parents=True, exist_ok=True)
        with self.run_log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
