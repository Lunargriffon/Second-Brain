"""SQLite-backed derivation jobs with atomic, recoverable leases."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pkb.knowledge.migrations import migrate


DEFAULT_LEASE = timedelta(minutes=15)
MAX_ATTEMPTS = 3


class LeaseOwnershipError(RuntimeError):
    """Raised when a worker tries to mutate a lease it does not own."""


@dataclass(frozen=True)
class Job:
    id: int
    job_type: str
    document_id: int
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
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO jobs
                   (job_type, document_id, input_hash, pipeline_version, status)
                   VALUES (?, ?, ?, ?, 'pending')""",
                (job_type, document_id, input_hash, pipeline_version),
            )
            row = self.connection.execute(
                """SELECT id FROM jobs WHERE job_type=? AND document_id=?
                   AND input_hash=? AND pipeline_version=?""",
                (job_type, document_id, input_hash, pipeline_version),
            ).fetchone()
            job_id = int(row[0])
            if cursor.rowcount == 1:
                self._event(job_id, "enqueued")
            self.connection.commit()
            return job_id
        except BaseException:
            self.connection.rollback()
            raise

    def claim(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        lease: timedelta = DEFAULT_LEASE,
    ) -> Job | None:
        instant = _utc(now)
        if lease <= timedelta(0):
            raise ValueError("lease must be positive")
        timestamp = _serialize(instant)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            while True:
                row = self.connection.execute(
                    """SELECT * FROM jobs
                       WHERE status='pending'
                          OR (status='running' AND lease_expires_at<=?)
                       ORDER BY created_at, id LIMIT 1""",
                    (timestamp,),
                ).fetchone()
                if row is None:
                    self.connection.commit()
                    return None
                job_id = int(row["id"])
                if int(row["attempts"]) >= MAX_ATTEMPTS:
                    if row["status"] == "running":
                        self._event(job_id, "lease_expired", row["worker_id"])
                    self.connection.execute(
                        """UPDATE jobs SET status='dead-letter', worker_id=NULL,
                           leased_at=NULL, lease_expires_at=NULL, heartbeat_at=NULL,
                           updated_at=? WHERE id=?""",
                        (timestamp, job_id),
                    )
                    self._event(job_id, "dead_lettered", details={"attempts": row["attempts"]})
                    continue
                if row["status"] == "running":
                    self._event(job_id, "lease_expired", row["worker_id"])
                expires = _serialize(instant + lease)
                self.connection.execute(
                    """UPDATE jobs SET status='running', attempts=attempts+1,
                       worker_id=?, leased_at=?, lease_expires_at=?, heartbeat_at=?,
                       error=NULL, updated_at=? WHERE id=?""",
                    (worker_id, timestamp, expires, timestamp, timestamp, job_id),
                )
                self._event(job_id, "claimed", worker_id, {"lease_expires_at": expires})
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

    def events(self, job_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM job_events WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(
            id=int(row["id"]), job_type=row["job_type"], document_id=int(row["document_id"]),
            input_hash=row["input_hash"], pipeline_version=row["pipeline_version"],
            status=row["status"], attempts=int(row["attempts"]), worker_id=row["worker_id"],
            leased_at=_parse(row["leased_at"]), lease_expires_at=_parse(row["lease_expires_at"]),
            heartbeat_at=_parse(row["heartbeat_at"]), error=row["error"],
        )
