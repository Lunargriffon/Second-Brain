"""Transactional, auditable persistence for Entity knowledge."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path

from .entity_models import (
    Alias,
    Entity,
    EntityMergeCycleError,
    EntityNotFoundError,
    InvalidEntityTransition,
    Mention,
)
from .migrations import migrate
from .text_versions import validate_span

_WHITESPACE = re.compile(r"\s+")


def _display_name(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip())


def _normalized(value: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).strip()).casefold()


def _actor_type(actor: str) -> str:
    prefix = actor.partition(":")[0]
    return {
        "human": "human",
        "deterministic": "deterministic_rule",
        "maintenance": "maintenance_task",
        "system": "system",
    }.get(prefix, "llm_candidate")


class EntityRepository:
    def __init__(self, database: str | Path):
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        migrate(self.connection)

    def __enter__(self) -> "EntityRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def create_candidate(
        self, entity_type: str, canonical_name: str, *, actor: str,
        derivation_id: str | None = None,
    ) -> int:
        display = _display_name(canonical_name)
        normalized = _normalized(canonical_name)
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO entities
                   (entity_type, canonical_name, normalized_name, creation_derivation_id)
                   VALUES (?, ?, ?, ?)""",
                (entity_type, display, normalized, derivation_id),
            )
            entity_id = int(cursor.lastrowid)
            self._event(
                "entity", entity_id, "created", actor, "entity candidate created",
                None, {"review_status": "pending"}, derivation_id=derivation_id,
            )
        return entity_id

    def get(self, entity_id: int) -> Entity:
        row = self.connection.execute(
            """SELECT id, entity_type, canonical_name, normalized_name,
                      review_status, merged_into_entity_id
               FROM entities WHERE id=?""",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise EntityNotFoundError(f"entity {entity_id} does not exist")
        return Entity(**dict(row))

    def accept(self, entity_id: int, *, actor: str, reason: str) -> Entity:
        return self._transition(entity_id, "pending", "accepted", actor, reason)

    def reject(self, entity_id: int, *, actor: str, reason: str) -> Entity:
        return self._transition(entity_id, "pending", "rejected", actor, reason)

    def reopen(self, entity_id: int, *, actor: str, reason: str) -> Entity:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.get(entity_id)
            if current.review_status != "accepted":
                raise InvalidEntityTransition(
                    f"cannot transition entity {entity_id} from {current.review_status} to pending"
                )
            fact_rows = self.connection.execute(
                """SELECT id, knowledge_status FROM facts
                   WHERE review_status='accepted'
                     AND (subject_entity_id=? OR object_entity_id=?) ORDER BY id""",
                (entity_id, entity_id),
            ).fetchall()
            fact_ids = tuple(int(row["id"]) for row in fact_rows)
            if fact_ids:
                placeholders = ",".join("?" for _ in fact_ids)
                relation_rows = self.connection.execute(
                    f"""SELECT id, knowledge_status FROM fact_relations
                         WHERE review_status='accepted'
                           AND (left_fact_id IN ({placeholders}) OR right_fact_id IN ({placeholders}))
                         ORDER BY id""",
                    fact_ids + fact_ids,
                ).fetchall()
                for row in relation_rows:
                    relation_id = int(row["id"])
                    self.connection.execute(
                        "UPDATE fact_relations SET review_status='pending', knowledge_status=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (relation_id,),
                    )
                    self._event(
                        "fact_relation", relation_id, "reopened", actor, reason,
                        {"review_status": "accepted", "knowledge_status": row["knowledge_status"]},
                        {"review_status": "pending", "knowledge_status": None},
                    )
            for row in fact_rows:
                fact_id = int(row["id"])
                self.connection.execute(
                    "UPDATE facts SET review_status='pending', knowledge_status=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (fact_id,),
                )
                self._event(
                    "fact", fact_id, "reopened", actor, reason,
                    {"review_status": "accepted", "knowledge_status": row["knowledge_status"]},
                    {"review_status": "pending", "knowledge_status": None},
                )
            self.connection.execute(
                "UPDATE entities SET merged_into_entity_id=NULL WHERE id=?", (entity_id,)
            )
            updated = self.connection.execute(
                """UPDATE entities SET review_status='pending', reviewed_at=CURRENT_TIMESTAMP,
                          reviewed_by=?, review_reason=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND review_status='accepted'""",
                (actor, reason, entity_id),
            )
            if updated.rowcount != 1:
                raise InvalidEntityTransition("entity state changed concurrently")
            self._event(
                "entity", entity_id, "reopened", actor, reason,
                {"review_status": "accepted"}, {"review_status": "pending"},
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(entity_id)

    def _transition(
        self, entity_id: int, expected: str, target: str, actor: str, reason: str,
    ) -> Entity:
        with self.connection:
            current = self.get(entity_id)
            if current.review_status != expected:
                raise InvalidEntityTransition(
                    f"cannot transition entity {entity_id} from "
                    f"{current.review_status} to {target}"
                )
            cursor = self.connection.execute(
                """UPDATE entities
                   SET review_status=?, reviewed_at=CURRENT_TIMESTAMP,
                       reviewed_by=?, review_reason=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND review_status=?""",
                (target, actor, reason, entity_id, expected),
            )
            if cursor.rowcount != 1:
                raise InvalidEntityTransition("entity state changed concurrently")
            event_type = {"accepted": "accepted", "rejected": "rejected", "pending": "reopened"}[target]
            self._event(
                "entity", entity_id, event_type, actor, reason,
                {"review_status": expected}, {"review_status": target},
            )
        return self.get(entity_id)

    def add_alias(
        self,
        entity_id: int,
        alias_kind: str,
        value: str,
        *,
        namespace: str = "",
        strong: bool = False,
        accept: bool = False,
        actor: str = "system:entity-repository",
        reason: str = "entity alias recorded",
    ) -> Alias:
        entity = self.get(entity_id)
        status = "accepted" if accept else "pending"
        if status == "accepted" and entity.review_status != "accepted":
            raise InvalidEntityTransition("accepted alias requires an accepted entity")
        display = _display_name(value)
        normalized = _normalized(value)
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO entity_aliases
                   (entity_id, alias_kind, alias_value, normalized_value, namespace,
                    is_strong_identifier, review_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (entity_id, alias_kind, display, normalized, namespace, int(strong), status),
            )
            alias_id = int(cursor.lastrowid)
            self._event(
                "entity_alias", alias_id, "accepted" if accept else "created",
                actor, reason, None, {"review_status": status, "entity_id": entity_id},
            )
        return Alias(alias_id, entity_id, alias_kind, display, normalized, namespace, strong, status)

    def add_mention(
        self,
        text_version_id: int,
        surface_text: str,
        start: int,
        end: int,
        mention_type: str,
        *,
        entity_id: int | None = None,
        method: str = "unresolved",
        accept: bool = False,
        confidence: float | None = None,
        actor: str = "system:entity-repository",
        reason: str = "entity mention recorded",
        derivation_id: str | None = None,
    ) -> Mention:
        version = self.connection.execute(
            """SELECT document_id, normalized_content_hash, normalization_version,
                      plain_content
               FROM document_text_versions WHERE id=?""",
            (text_version_id,),
        ).fetchone()
        if version is None:
            raise LookupError(f"document text version {text_version_id} does not exist")
        validate_span(version["plain_content"], start, end, surface_text)
        if entity_id is not None:
            entity = self.get(entity_id)
            if accept and entity.review_status != "accepted":
                raise InvalidEntityTransition("accepted mention requires an accepted entity")
        status = "accepted" if accept and method == "strong_identifier" else "pending"
        if entity_id is None:
            method = "unresolved"
            status = "pending"
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO entity_mentions
                   (document_text_version_id, document_id, normalized_content_hash,
                    normalization_version, entity_id, surface_text, start_offset,
                    end_offset, mention_type, linking_method, linking_confidence,
                    review_status, derivation_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    text_version_id, version["document_id"],
                    version["normalized_content_hash"], version["normalization_version"],
                    entity_id, surface_text, start, end, mention_type, method,
                    confidence, status, derivation_id,
                ),
            )
            mention_id = int(cursor.lastrowid)
            self._event(
                "entity_mention", mention_id,
                "accepted" if status == "accepted" else "created", actor, reason,
                None, {"review_status": status, "entity_id": entity_id},
                derivation_id=derivation_id,
            )
        return Mention(
            mention_id, int(version["document_id"]), entity_id, surface_text,
            start, end, status,
        )

    def resolve_canonical(self, entity_id: int) -> int:
        seen: set[int] = set()
        current = entity_id
        while True:
            if current in seen:
                raise EntityMergeCycleError(f"canonical entity chain for {entity_id} contains a cycle")
            seen.add(current)
            entity = self.get(current)
            if entity.merged_into_entity_id is None:
                return current
            current = entity.merged_into_entity_id

    def merge(
        self,
        loser_entity_id: int,
        winner_entity_id: int,
        *,
        actor: str,
        reason: str,
    ) -> int:
        """Merge an accepted Entity into a canonical endpoint without rewriting history."""
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            loser = self.get(loser_entity_id)
            winner = self.get(winner_entity_id)
            if loser.review_status != "accepted" or winner.review_status != "accepted":
                raise InvalidEntityTransition("entity merge requires accepted entities")
            if loser.merged_into_entity_id is not None:
                raise InvalidEntityTransition("loser entity is already merged")

            canonical_winner_id = self.resolve_canonical(winner_entity_id)
            if canonical_winner_id == loser_entity_id:
                raise EntityMergeCycleError(
                    f"merging entity {loser_entity_id} into {winner_entity_id} would form a cycle"
                )

            cursor = self.connection.execute(
                """INSERT INTO entity_merge_events
                   (loser_entity_id, winner_entity_id, action, reason, actor)
                   VALUES (?, ?, 'merge', ?, ?)""",
                (loser_entity_id, canonical_winner_id, reason, actor),
            )
            event_id = int(cursor.lastrowid)
            updated = self.connection.execute(
                """UPDATE entities
                   SET merged_into_entity_id=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND merged_into_entity_id IS NULL
                         AND review_status='accepted'""",
                (canonical_winner_id, loser_entity_id),
            )
            if updated.rowcount != 1:
                raise InvalidEntityTransition("entity merge state changed concurrently")
            self._event(
                "entity_merge", event_id, "merged", actor, reason,
                {"loser_entity_id": loser_entity_id, "merged_into_entity_id": None},
                {
                    "loser_entity_id": loser_entity_id,
                    "merged_into_entity_id": canonical_winner_id,
                },
            )
            self.connection.commit()
            return event_id
        except Exception:
            self.connection.rollback()
            raise

    def undo_merge(
        self,
        merge_event_id: int,
        *,
        actor: str,
        reason: str,
    ) -> int:
        """Reverse the current pointer created by one merge event."""
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            event = self.connection.execute(
                """SELECT id, loser_entity_id, winner_entity_id, action
                   FROM entity_merge_events WHERE id=?""",
                (merge_event_id,),
            ).fetchone()
            if event is None or event["action"] != "merge":
                raise InvalidEntityTransition("merge event does not exist")
            reversed_row = self.connection.execute(
                "SELECT 1 FROM entity_merge_events WHERE reverses_event_id=?",
                (merge_event_id,),
            ).fetchone()
            if reversed_row is not None:
                raise InvalidEntityTransition("merge event is already reversed")
            loser = self.get(int(event["loser_entity_id"]))
            winner_id = int(event["winner_entity_id"])
            if loser.merged_into_entity_id != winner_id:
                raise InvalidEntityTransition("current entity merge does not match event")

            cursor = self.connection.execute(
                """INSERT INTO entity_merge_events
                   (loser_entity_id, winner_entity_id, action, reverses_event_id,
                    reason, actor)
                   VALUES (?, ?, 'undo', ?, ?, ?)""",
                (loser.id, winner_id, merge_event_id, reason, actor),
            )
            undo_event_id = int(cursor.lastrowid)
            updated = self.connection.execute(
                """UPDATE entities
                   SET merged_into_entity_id=NULL, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND merged_into_entity_id=?""",
                (loser.id, winner_id),
            )
            if updated.rowcount != 1:
                raise InvalidEntityTransition("entity merge state changed concurrently")
            self._event(
                "entity_merge", undo_event_id, "merge_undone", actor, reason,
                {"loser_entity_id": loser.id, "merged_into_entity_id": winner_id},
                {"loser_entity_id": loser.id, "merged_into_entity_id": None},
            )
            self.connection.commit()
            return undo_event_id
        except Exception:
            self.connection.rollback()
            raise

    def _event(
        self,
        object_type: str,
        object_id: int,
        event_type: str,
        actor: str,
        reason: str,
        previous_state: dict[str, object] | None,
        new_state: dict[str, object] | None,
        *,
        derivation_id: str | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO knowledge_events
               (object_type, object_id, event_type, actor_type, actor_id, reason,
                derivation_id, previous_state_json, new_state_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                object_type, object_id, event_type, _actor_type(actor), actor, reason,
                derivation_id,
                json.dumps(previous_state, ensure_ascii=False, sort_keys=True) if previous_state is not None else None,
                json.dumps(new_state, ensure_ascii=False, sort_keys=True) if new_state is not None else None,
            ),
        )


__all__ = [
    "EntityRepository",
    "InvalidEntityTransition",
    "EntityMergeCycleError",
    "EntityNotFoundError",
]
