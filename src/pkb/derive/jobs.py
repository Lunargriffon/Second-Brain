"""SQLite-backed derivation jobs with atomic, recoverable leases."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

from pkb.knowledge.migrations import migrate
from pkb.knowledge.job_scopes import (
    SCOPE_TYPES,
    JobScope,
    canonical_scopes,
    scope_hash,
)


DEFAULT_LEASE = timedelta(minutes=15)
MAX_ATTEMPTS = 3
T = TypeVar("T")


class LeaseOwnershipError(RuntimeError):
    """Raised when a worker tries to mutate a lease it does not own."""


@dataclass(frozen=True)
class Job:
    id: int
    job_type: str
    document_id: int | None
    scope_hash: str
    input_hash: str
    pipeline_version: str
    status: str
    attempts: int
    worker_id: str | None
    leased_at: datetime | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    error: str | None


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("job timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _serialize(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


class JobQueue:
    def __init__(self, database: str | Path, *, timeout: float = 5.0):
        self.connection = sqlite3.connect(database, timeout=timeout, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        migrate(self.connection)

    def __enter__(self) -> "JobQueue":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def enqueue(
        self, job_type: str, document_id: int, input_hash: str, pipeline_version: str
    ) -> int:
        return self.enqueue_scoped(
            job_type,
            (JobScope("document", str(document_id), "anchor", 0),),
            input_hash,
            pipeline_version,
        )

    def enqueue_scoped(
        self,
        job_type: str,
        scopes: Iterable[JobScope],
        input_hash: str,
        pipeline_version: str,
    ) -> int:
        if not job_type or not job_type.strip():
            raise ValueError("job_type must be non-empty")
        if not input_hash:
            raise ValueError("input_hash must be non-empty")
        if not pipeline_version or not pipeline_version.strip():
            raise ValueError("pipeline_version must be non-empty")
        values = canonical_scopes(scopes)
        identity = scope_hash(values)
        anchor = next(scope for scope in values if scope.scope_role == "anchor")
        document_id: int | None = None
        if anchor.scope_type == "document":
            if not anchor.scope_id.isascii() or not anchor.scope_id.isdecimal():
                raise ValueError("document anchor scope_id must be decimal")
            document_id = int(anchor.scope_id)
            if self.connection.execute(
                "SELECT 1 FROM documents WHERE id=?", (document_id,)
            ).fetchone() is None:
                raise ValueError(f"document anchor does not exist: {document_id}")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO jobs
                   (job_type, document_id, scope_hash, input_hash, pipeline_version, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (job_type, document_id, identity, input_hash, pipeline_version),
            )
            row = self.connection.execute(
                """SELECT id FROM jobs WHERE job_type=? AND scope_hash=?
                   AND input_hash=? AND pipeline_version=?""",
                (job_type, identity, input_hash, pipeline_version),
            ).fetchone()
            if row is None:
                raise RuntimeError("scoped job could not be inserted or located")
            job_id = int(row[0])
            for scope in values:
                self.connection.execute(
                    """INSERT OR IGNORE INTO job_scopes
                       (job_id, scope_type, scope_id, scope_role, ordinal)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        job_id,
                        scope.scope_type,
                        scope.scope_id,
                        scope.scope_role,
                        scope.ordinal,
                    ),
                )
            if self.scopes(job_id) != values:
                raise RuntimeError("persisted job scopes do not match requested scopes")
            if cursor.rowcount == 1:
                self._event(job_id, "enqueued", details={"scope_hash": identity})
            self.connection.commit()
            return job_id
        except BaseException:
            self.connection.rollback()
            raise

    def claim(
        self,
        worker_id: str,
        *,
        job_type: str | None = None,
        anchor_type: str | None = None,
        now: datetime | None = None,
        lease: timedelta = DEFAULT_LEASE,
    ) -> Job | None:
        instant = _utc(now)
        if lease <= timedelta(0):
            raise ValueError("lease must be positive")
        if anchor_type is not None and anchor_type not in SCOPE_TYPES:
            raise ValueError(f"invalid anchor_type: {anchor_type!r}")
        timestamp = _serialize(instant)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            while True:
                type_clause = " AND job_type=?" if job_type is not None else ""
                anchor_clause = ""
                parameters: list[object] = [timestamp]
                if job_type is not None:
                    parameters.append(job_type)
                if anchor_type is not None:
                    anchor_clause = """ AND EXISTS (
                        SELECT 1 FROM job_scopes AS scope
                        WHERE scope.job_id=jobs.id
                          AND scope.scope_role='anchor'
                          AND scope.scope_type=?
                    )"""
                    parameters.append(anchor_type)
                row = self.connection.execute(
                    """SELECT * FROM jobs
                       WHERE (status='pending'
                          OR (status='running' AND lease_expires_at<=?))"""
                    + type_clause + anchor_clause + " ORDER BY created_at, id LIMIT 1",
                    parameters,
                ).fetchone()
                if row is None:
                    self.connection.commit()
                    return None
                job_id = int(row["id"])
                if int(row["attempts"]) >= MAX_ATTEMPTS:
                    if row["status"] == "running":
                        self._event(
                            job_id,
                            "lease_expired",
                            row["worker_id"],
                            {"scope_hash": row["scope_hash"]},
                        )
                    self.connection.execute(
                        """UPDATE jobs SET status='dead-letter', worker_id=NULL,
                           leased_at=NULL, lease_expires_at=NULL, heartbeat_at=NULL,
                           updated_at=? WHERE id=?""",
                        (timestamp, job_id),
                    )
                    self._event(
                        job_id,
                        "dead_lettered",
                        details={
                            "attempts": row["attempts"],
                            "scope_hash": row["scope_hash"],
                        },
                    )
                    continue
                if row["status"] == "running":
                    self._event(
                        job_id,
                        "lease_expired",
                        row["worker_id"],
                        {"scope_hash": row["scope_hash"]},
                    )
                expires = _serialize(instant + lease)
                self.connection.execute(
                    """UPDATE jobs SET status='running', attempts=attempts+1,
                       worker_id=?, leased_at=?, lease_expires_at=?, heartbeat_at=?,
                       error=NULL, updated_at=? WHERE id=?""",
                    (worker_id, timestamp, expires, timestamp, timestamp, job_id),
                )
                self._event(
                    job_id,
                    "claimed",
                    worker_id,
                    {
                        "lease_expires_at": expires,
                        "scope_hash": row["scope_hash"],
                    },
                )
                claimed = self.connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                self.connection.commit()
                return self._job(claimed)
        except BaseException:
            self.connection.rollback()
            raise

    def heartbeat(
        self, job_id: int, worker_id: str, *, now: datetime | None = None,
        lease: timedelta = DEFAULT_LEASE,
    ) -> Job:
        instant = _utc(now)
        if lease <= timedelta(0):
            raise ValueError("lease must be positive")
        timestamp, expires = _serialize(instant), _serialize(instant + lease)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._require_owner(job_id, worker_id, timestamp)
            self.connection.execute(
                "UPDATE jobs SET heartbeat_at=?, lease_expires_at=?, updated_at=? WHERE id=?",
                (timestamp, expires, timestamp, job_id),
            )
            self._event(job_id, "heartbeat", worker_id, {"lease_expires_at": expires})
            row = self.connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            self.connection.commit()
            return self._job(row)
        except BaseException:
            self.connection.rollback()
            raise

    def succeed(self, job_id: int, worker_id: str, *, now: datetime | None = None) -> None:
        self._finish(job_id, worker_id, status="succeeded", event="succeeded", now=now)

    def publish_and_succeed(
        self,
        job_id: int,
        worker_id: str,
        publish: Callable[[sqlite3.Connection], T],
        *,
        now: datetime | None = None,
    ) -> T:
        """Publish domain rows and finish an owned job in one transaction.

        ``publish`` must use the supplied connection. It must not commit, roll
        back, close that connection, or perform writes through another
        connection; doing so would break the atomic publication boundary.
        """
        timestamp = _serialize(_utc(now))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._require_owner(job_id, worker_id, timestamp)
            result = publish(self.connection)
            self._require_owner(job_id, worker_id, timestamp)
            self.connection.execute(
                """UPDATE jobs SET status='succeeded', worker_id=NULL,
                   leased_at=NULL, lease_expires_at=NULL, heartbeat_at=NULL,
                   error=NULL, updated_at=? WHERE id=?""",
                (timestamp, job_id),
            )
            self._event(job_id, "succeeded", worker_id)
            self.connection.commit()
            return result
        except BaseException:
            self.connection.rollback()
            raise

    def fail(
        self, job_id: int, worker_id: str, error: str, *, now: datetime | None = None
    ) -> None:
        self._finish(job_id, worker_id, status="failed", event="failed", now=now, error=error)

    def _finish(self, job_id: int, worker_id: str, *, status: str, event: str,
                now: datetime | None, error: str | None = None) -> None:
        timestamp = _serialize(_utc(now))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._require_owner(job_id, worker_id, timestamp)
            self.connection.execute(
                """UPDATE jobs SET status=?, worker_id=NULL, leased_at=NULL,
                   lease_expires_at=NULL, heartbeat_at=NULL, error=?, updated_at=? WHERE id=?""",
                (status, error, timestamp, job_id),
            )
            self._event(job_id, event, worker_id, {"error": error} if error else None)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _require_owner(self, job_id: int, worker_id: str, now: str) -> None:
        row = self.connection.execute(
            "SELECT status, worker_id, lease_expires_at FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if (
            row is None or row["status"] != "running" or row["worker_id"] != worker_id
            or row["lease_expires_at"] <= now
        ):
            raise LeaseOwnershipError(f"worker does not own active lease for job {job_id}")

    def _event(self, job_id: int, event_type: str, worker_id: str | None = None,
               details: dict[str, object] | None = None) -> None:
        self.connection.execute(
            "INSERT INTO job_events(job_id, event_type, worker_id, details_json) VALUES (?, ?, ?, ?)",
            (job_id, event_type, worker_id, json.dumps(details, sort_keys=True) if details else None),
        )

    def get(self, job_id: int) -> Job:
        row = self.connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._job(row)

    def scopes(self, job_id: int) -> tuple[JobScope, ...]:
        rows = self.connection.execute(
            """SELECT scope_type, scope_id, scope_role, ordinal
               FROM job_scopes WHERE job_id=?
               ORDER BY scope_role, ordinal, scope_type, scope_id""",
            (job_id,),
        ).fetchall()
        if not rows:
            if self.connection.execute(
                "SELECT 1 FROM jobs WHERE id=?", (job_id,)
            ).fetchone() is None:
                raise KeyError(job_id)
            raise RuntimeError(f"job {job_id} has no scopes")
        return canonical_scopes(
            JobScope(row[0], row[1], row[2], int(row[3])) for row in rows
        )

    def events(self, job_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM job_events WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(
            id=int(row["id"]), job_type=row["job_type"],
            document_id=(int(row["document_id"]) if row["document_id"] is not None else None),
            scope_hash=row["scope_hash"],
            input_hash=row["input_hash"], pipeline_version=row["pipeline_version"],
            status=row["status"], attempts=int(row["attempts"]), worker_id=row["worker_id"],
            leased_at=_parse(row["leased_at"]), lease_expires_at=_parse(row["lease_expires_at"]),
            heartbeat_at=_parse(row["heartbeat_at"]), error=row["error"],
        )
