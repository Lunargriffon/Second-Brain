"""Transactional persistence for normalized knowledge documents."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .migrations import migrate
from .models import NormalizedDocument
from .urls import canonicalize_url, platform_identity


class MergeConflictError(ValueError):
    """Raised when a merge would silently discard human-owned state."""


@dataclass(frozen=True)
class UpsertResult:
    document_id: int
    created: bool
    normalized_changed: bool
    source_changed: bool


class KnowledgeRepository:
    def __init__(self, database: str | Path):
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        migrate(self.connection)

    def __enter__(self) -> "KnowledgeRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def upsert_document(
        self,
        document: NormalizedDocument,
        *,
        source_hash: str,
        normalized_hash: str,
        normalization_version: int,
        commit: bool = True,
    ) -> UpsertResult:
        canonical_url = canonicalize_url(document.canonical_url) if document.canonical_url else ""
        with self.connection if commit else nullcontext():
            existing = self._find_document(document.identity_key, canonical_url, source_hash)
            created = existing is None
            if created:
                cursor = self.connection.execute(
                    """INSERT INTO documents
                       (identity_key, source_type, title, author, plain_content,
                        canonical_url, source_created_at, source_content_hash,
                        normalized_content_hash, normalization_version, schema_version,
                        source_observed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (
                        document.identity_key,
                        document.membership.source,
                        document.title,
                        document.author,
                        document.plain_content,
                        canonical_url,
                        document.source_created_at,
                        source_hash,
                        normalized_hash,
                        normalization_version,
                        document.source_observed_at,
                    ),
                )
                document_id = int(cursor.lastrowid)
                source_changed = normalized_changed = True
            else:
                document_id = int(existing["id"])
                incoming_key = self._winner_key(
                    document.membership.source, document.source_observed_at, source_hash
                )
                existing_key = self._winner_key(
                    existing["source_type"], existing["source_observed_at"],
                    existing["source_content_hash"],
                )
                incoming_wins = incoming_key >= existing_key
                source_changed = incoming_wins and existing["source_content_hash"] != source_hash
                normalized_changed = incoming_wins and (
                    existing["normalized_content_hash"] != normalized_hash
                    or existing["normalization_version"] != normalization_version
                )
                winner_metadata_changed = (
                    incoming_wins
                    and existing["source_observed_at"] != document.source_observed_at
                )
                if incoming_wins and (source_changed or normalized_changed or winner_metadata_changed):
                    self.connection.execute(
                    """UPDATE documents SET title=?, author=?, plain_content=?,
                       canonical_url=?, source_created_at=?, source_type=?, source_content_hash=?,
                       normalized_content_hash=?, normalization_version=?, source_observed_at=?,
                       updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (
                        document.title,
                        document.author,
                        document.plain_content,
                        canonical_url,
                        document.source_created_at,
                        document.membership.source,
                        source_hash,
                        normalized_hash,
                        normalization_version,
                        document.source_observed_at,
                        document_id,
                    ),
                    )

            if canonical_url:
                self.connection.execute(
                    """INSERT INTO document_url_aliases(document_id, url, observed_url, source)
                       VALUES (?, ?, ?, ?) ON CONFLICT(url) DO NOTHING""",
                    (document_id, canonical_url, document.canonical_url, document.membership.source),
                )
            membership = document.membership
            membership_owner = self.connection.execute(
                """SELECT document_id FROM source_memberships
                   WHERE source=? AND source_item_id=? AND collection_id=?""",
                (membership.source, membership.source_item_id, membership.collection_id or ""),
            ).fetchone()
            if membership_owner and int(membership_owner[0]) != document_id:
                raise MergeConflictError("membership evidence already belongs to another document")
            self.connection.execute(
                """INSERT INTO source_memberships
                   (document_id, source, source_item_id, collection_id, raw_path,
                    raw_line, source_url, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, source_item_id, collection_id) DO UPDATE SET
                     document_id=excluded.document_id, raw_path=excluded.raw_path,
                     raw_line=excluded.raw_line, source_url=excluded.source_url,
                     observed_at=excluded.observed_at""",
                (
                    document_id,
                    membership.source,
                    membership.source_item_id,
                    membership.collection_id or "",
                    str(membership.raw_path),
                    membership.raw_line,
                    membership.source_url,
                    document.source_observed_at,
                ),
            )
            if created or source_changed:
                self.connection.execute("DELETE FROM media WHERE document_id=?", (document_id,))
            for order, remote_url in enumerate(document.media_urls if created or source_changed else ()):
                self.connection.execute(
                    """INSERT INTO media(document_id, remote_url, media_order)
                       VALUES (?, ?, ?) ON CONFLICT(document_id, remote_url)
                       DO UPDATE SET media_order=excluded.media_order""",
                    (document_id, remote_url, order),
                )
        return UpsertResult(document_id, created, normalized_changed, source_changed)

    def _find_document(self, identity_key: str, canonical_url: str, source_hash: str) -> sqlite3.Row | None:
        candidate_ids: set[int] = set()
        identities = {identity_key}
        if canonical_url and (recognized := platform_identity(canonical_url)):
            identities.add(recognized)
        for candidate_identity in identities:
            rows = self.connection.execute(
                """SELECT id FROM documents WHERE identity_key=?
                   UNION SELECT canonical_document_id FROM document_identity_aliases
                   WHERE alias_identity_key=?""",
                (candidate_identity, candidate_identity),
            ).fetchall()
            candidate_ids.update(int(row[0]) for row in rows)
        if canonical_url:
            row = self.connection.execute(
                "SELECT document_id FROM document_url_aliases WHERE url=?", (canonical_url,)
            ).fetchone()
            if row:
                candidate_ids.add(int(row[0]))
        if not candidate_ids and identity_key.startswith("url:"):
            rows = self.connection.execute(
                "SELECT id FROM documents WHERE identity_key LIKE 'url:%' AND source_content_hash=?",
                (source_hash,),
            ).fetchall()
            candidate_ids.update(int(row[0]) for row in rows)
        if len(candidate_ids) > 1:
            raise MergeConflictError("identity candidates disagree")
        if not candidate_ids:
            return None
        return self.connection.execute("SELECT * FROM documents WHERE id=?", (candidate_ids.pop(),)).fetchone()

    @staticmethod
    def _source_rank(source: str | None) -> int:
        return {"x": 10, "zhihu": 20}.get(source or "", 0)

    @classmethod
    def _winner_key(
        cls, source: str | None, observed_at: str | None, source_hash: str
    ) -> tuple[int, str, str]:
        """Choose same-source evidence deterministically; timestamps outrank hashes."""
        return (cls._source_rank(source), observed_at or "", source_hash)

    def merge_documents(
        self,
        survivor_id: int,
        duplicate_id: int,
        *,
        reason: str,
        reading_state_policy: str = "reject",
    ) -> int:
        if reading_state_policy not in {"reject", "survivor", "newest_reviewed"}:
            raise ValueError("unknown reading_state_policy")
        if survivor_id == duplicate_id:
            raise ValueError("cannot merge a document into itself")
        with self.connection:
            self._preflight_merge(survivor_id, duplicate_id)
            survivor = self._state(survivor_id)
            duplicate = self._state(duplicate_id)
            self._ensure_notes_compatible(survivor, duplicate)
            chosen = self._choose_state(survivor, duplicate, reading_state_policy)
            metadata = json.dumps(
                {"survivor": dict(survivor) if survivor else None, "duplicate": dict(duplicate) if duplicate else None},
                sort_keys=True,
            )
            cursor = self.connection.execute(
                """INSERT INTO document_merges
                   (survivor_document_id, duplicate_document_id, reason,
                    reading_state_policy, metadata_json) VALUES (?, ?, ?, ?, ?)""",
                (survivor_id, duplicate_id, reason, reading_state_policy, metadata),
            )
            merge_id = int(cursor.lastrowid)
            self._move_simple("source_memberships", survivor_id, duplicate_id, ("source", "source_item_id", "collection_id"))
            self._move_simple("document_url_aliases", survivor_id, duplicate_id, ("url",))
            self._move_simple("media", survivor_id, duplicate_id, ("remote_url",))
            self._move_user_tags(survivor_id, duplicate_id)
            self.connection.execute("DELETE FROM reading_state WHERE document_id IN (?, ?)", (survivor_id, duplicate_id))
            if chosen:
                self._insert_state(survivor_id, chosen)
            self.connection.execute(
                "INSERT INTO document_id_aliases(alias_document_id, canonical_document_id, merge_id) VALUES (?, ?, ?)",
                (duplicate_id, survivor_id, merge_id),
            )
            duplicate_identity = self.connection.execute(
                "SELECT identity_key FROM documents WHERE id=?", (duplicate_id,)
            ).fetchone()[0]
            self.connection.execute(
                """INSERT INTO document_identity_aliases(alias_identity_key, canonical_document_id, merge_id)
                   VALUES (?, ?, ?) ON CONFLICT(alias_identity_key) DO UPDATE SET
                   canonical_document_id=excluded.canonical_document_id, merge_id=excluded.merge_id""",
                (duplicate_identity, survivor_id, merge_id),
            )
            self._assert_no_duplicate_children(duplicate_id)
            self.connection.execute("DELETE FROM documents WHERE id=?", (duplicate_id,))
        return merge_id

    def _move_simple(self, table: str, survivor: int, duplicate: int, conflict: tuple[str, ...]) -> None:
        rows = self.connection.execute(f"SELECT * FROM {table} WHERE document_id=?", (duplicate,)).fetchall()
        for row in rows:
            where = " AND ".join(f"{column}=?" for column in conflict)
            values = tuple(row[column] for column in conflict)
            exists = self.connection.execute(
                f"SELECT 1 FROM {table} WHERE document_id=? AND {where}", (survivor, *values)
            ).fetchone()
            if exists:
                self.connection.execute(f"DELETE FROM {table} WHERE id=?", (row["id"],))
            else:
                self.connection.execute(f"UPDATE {table} SET document_id=? WHERE id=?", (survivor, row["id"]))

    def _move_user_tags(self, survivor: int, duplicate: int) -> None:
        self.connection.execute(
            """INSERT OR IGNORE INTO document_tags(document_id, tag_id, origin, confidence, derivation_id, created_at)
               SELECT ?, tag_id, origin, confidence, derivation_id, created_at FROM document_tags
               WHERE document_id=? AND origin='user'""",
            (survivor, duplicate),
        )
        self.connection.execute("DELETE FROM document_tags WHERE document_id=? AND origin='user'", (duplicate,))
        self.connection.execute(
            "UPDATE document_id_aliases SET canonical_document_id=? WHERE canonical_document_id=?",
            (survivor, duplicate),
        )
        self.connection.execute(
            "UPDATE document_identity_aliases SET canonical_document_id=? WHERE canonical_document_id=?",
            (survivor, duplicate),
        )

    def _preflight_merge(self, survivor: int, duplicate: int) -> None:
        ids = (survivor, duplicate)
        unsafe = (
            self.connection.execute("SELECT 1 FROM derivations WHERE document_id IN (?, ?) LIMIT 1", ids).fetchone()
            or self.connection.execute("SELECT 1 FROM jobs WHERE document_id IN (?, ?) LIMIT 1", ids).fetchone()
            or self.connection.execute("SELECT 1 FROM relations WHERE source_document_id IN (?, ?) OR target_document_id IN (?, ?) LIMIT 1", (*ids, *ids)).fetchone()
            or self.connection.execute("SELECT 1 FROM document_tags WHERE document_id IN (?, ?) AND origin<>'user' LIMIT 1", ids).fetchone()
            or self.connection.execute("SELECT 1 FROM document_topics WHERE document_id IN (?, ?) LIMIT 1", ids).fetchone()
        )
        if unsafe:
            raise MergeConflictError("documents have derived or queued child records")
        self._reject_evidence_conflicts("source_memberships", survivor, duplicate, ("source", "source_item_id", "collection_id"))
        self._reject_evidence_conflicts("media", survivor, duplicate, ("remote_url",))
        survivor_document = self.document(survivor)
        if survivor_document and survivor_document["canonical_url"]:
            alias = self.connection.execute(
                "SELECT * FROM document_url_aliases WHERE document_id=? AND url=?",
                (duplicate, survivor_document["canonical_url"]),
            ).fetchone()
            if alias and (
                alias["observed_url"] != survivor_document["canonical_url"]
                or alias["source"] != survivor_document["source_type"]
            ):
                raise MergeConflictError("conflicting URL alias evidence")

    def _reject_evidence_conflicts(self, table: str, survivor: int, duplicate: int, keys: tuple[str, ...]) -> None:
        duplicate_rows = self.connection.execute(f"SELECT * FROM {table} WHERE document_id=?", (duplicate,)).fetchall()
        for row in duplicate_rows:
            where = " AND ".join(f"{key}=?" for key in keys)
            other = self.connection.execute(
                f"SELECT * FROM {table} WHERE document_id=? AND {where}",
                (survivor, *(row[key] for key in keys)),
            ).fetchone()
            if other:
                compared = set(row.keys()) - {"id", "document_id"}
                if any(row[column] != other[column] for column in compared):
                    raise MergeConflictError(f"conflicting {table} evidence")

    def _assert_no_duplicate_children(self, duplicate: int) -> None:
        checks = {
            "source_memberships": "document_id", "document_url_aliases": "document_id",
            "media": "document_id", "derivations": "document_id", "document_tags": "document_id",
            "document_topics": "document_id", "reading_state": "document_id", "jobs": "document_id",
            "relations": "source_document_id", "document_id_aliases": "canonical_document_id",
            "document_identity_aliases": "canonical_document_id",
        }
        for table, column in checks.items():
            if self.connection.execute(f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (duplicate,)).fetchone():
                raise RuntimeError(f"merge left duplicate child reference in {table}")
        if self.connection.execute("SELECT 1 FROM relations WHERE target_document_id=? LIMIT 1", (duplicate,)).fetchone():
            raise RuntimeError("merge left duplicate child reference in relations")

    def _state(self, document_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM reading_state WHERE document_id=?", (document_id,)).fetchone()

    @staticmethod
    def _explicit(state: sqlite3.Row | None) -> bool:
        return bool(state and (state["status"] != "unread" or state["manual_priority"] is not None or state["priority_reason"] or state["last_reviewed_at"] or state["user_note_ref"]))

    def _ensure_notes_compatible(self, left: sqlite3.Row | None, right: sqlite3.Row | None) -> None:
        if left and right and left["user_note_ref"] and right["user_note_ref"] and left["user_note_ref"] != right["user_note_ref"]:
            raise MergeConflictError("conflicting human notes")

    def _choose_state(self, left: sqlite3.Row | None, right: sqlite3.Row | None, policy: str) -> sqlite3.Row | dict[str, Any] | None:
        if not self._explicit(left):
            return right or left
        if not self._explicit(right):
            return left
        comparable = ("status", "manual_priority", "priority_reason")
        if all(left[key] == right[key] for key in comparable):
            newer = max((left, right), key=lambda row: row["last_reviewed_at"] or "")
            combined = dict(newer)
            combined["user_note_ref"] = left["user_note_ref"] or right["user_note_ref"]
            return combined
        if policy == "reject":
            raise MergeConflictError("conflicting reading state")
        if policy == "survivor":
            return left
        return max((left, right), key=lambda row: row["last_reviewed_at"] or "")

    def _insert_state(self, document_id: int, state: sqlite3.Row | dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT INTO reading_state(document_id, status, manual_priority, priority_reason,
               last_reviewed_at, user_note_ref, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (document_id, state["status"], state["manual_priority"], state["priority_reason"], state["last_reviewed_at"], state["user_note_ref"], state["updated_at"]),
        )

    def set_reading_state(self, document_id: int, **values: Any) -> None:
        allowed = {"status", "manual_priority", "priority_reason", "last_reviewed_at", "user_note_ref"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown reading state fields: {sorted(unknown)}")
        current = dict(self._state(document_id) or {})
        state = {"status": "unread", "manual_priority": None, "priority_reason": None, "last_reviewed_at": None, "user_note_ref": None, **current, **values}
        with self.connection:
            self.connection.execute("DELETE FROM reading_state WHERE document_id=?", (document_id,))
            self.connection.execute(
                """INSERT INTO reading_state(document_id, status, manual_priority, priority_reason,
                   last_reviewed_at, user_note_ref) VALUES (?, ?, ?, ?, ?, ?)""",
                (document_id, state["status"], state["manual_priority"], state["priority_reason"], state["last_reviewed_at"], state["user_note_ref"]),
            )

    def count_documents(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def enqueue_derivation_job(
        self, document_id: int, *, input_hash: str, pipeline_version: str,
        commit: bool = True,
    ) -> bool:
        """Queue one pending article job, returning whether it was newly created."""
        with self.connection if commit else nullcontext():
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO jobs
                   (job_type, document_id, input_hash, pipeline_version, status)
                   VALUES ('article', ?, ?, ?, 'pending')""",
                (document_id, input_hash, pipeline_version),
            )
        return cursor.rowcount == 1

    def document(self, document_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()

    def count_memberships(self, document_id: int) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM source_memberships WHERE document_id=?", (document_id,)).fetchone()[0]

    def memberships(self, document_id: int) -> list[sqlite3.Row]:
        return self.connection.execute("SELECT * FROM source_memberships WHERE document_id=? ORDER BY id", (document_id,)).fetchall()

    def count_media(self, document_id: int) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM media WHERE document_id=?", (document_id,)).fetchone()[0]

    def count_merges(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM document_merges").fetchone()[0]

    def resolve_document_id(self, document_id: int) -> int:
        row = self.connection.execute("SELECT canonical_document_id FROM document_id_aliases WHERE alias_document_id=?", (document_id,)).fetchone()
        return int(row[0]) if row else document_id

    def resolve_identity(self, identity_key: str) -> int | None:
        row = self.connection.execute(
            "SELECT id FROM documents WHERE identity_key=?", (identity_key,)
        ).fetchone()
        if row:
            return int(row[0])
        row = self.connection.execute(
            "SELECT canonical_document_id FROM document_identity_aliases WHERE alias_identity_key=?",
            (identity_key,),
        ).fetchone()
        return int(row[0]) if row else None

    def reading_state(self, document_id: int) -> sqlite3.Row | None:
        return self._state(document_id)

    def latest_merge(self) -> sqlite3.Row:
        return self.connection.execute("SELECT * FROM document_merges ORDER BY id DESC LIMIT 1").fetchone()
