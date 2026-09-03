"""Transactional persistence for typed, evidence-grounded Facts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .entity_repository import EntityRepository
from .fact_models import (
    EntityObject,
    Evidence,
    Fact,
    FactRelation,
    ScalarObject,
    canonical_object,
    fact_key,
    normalize_predicate,
)
from .migrations import migrate
from .text_versions import validate_span


class FactNotFoundError(LookupError):
    """Raised when a Fact does not exist."""


class FactPublicationError(ValueError):
    """Raised when a Fact cannot safely enter the accepted knowledge layer."""


class InvalidFactTransition(ValueError):
    """Raised when a Fact review-state transition is not allowed."""


class FactRelationDecisionError(ValueError):
    """Raised when a relation is invalid or an actor cannot decide it."""


class FactRelationCycleError(FactRelationDecisionError):
    """Raised when accepting supersession would create a directed cycle."""


@dataclass(frozen=True)
class InvalidationResult:
    """A bounded invalidation batch suitable for later synthesis propagation."""

    mention_ids: tuple[int, ...] = ()
    evidence_ids: tuple[int, ...] = ()
    entity_ids: tuple[int, ...] = ()
    fact_ids: tuple[int, ...] = ()
    relation_ids: tuple[int, ...] = ()


def _actor_type(actor: str) -> str:
    return {
        "human": "human",
        "deterministic": "deterministic_rule",
        "maintenance": "maintenance_task",
        "system": "system",
    }.get(actor.partition(":")[0], "llm_candidate")


class FactRepository:
    def __init__(self, database: str | Path):
        self.database = Path(database)
        self._owns_connection = True
        self.connection = sqlite3.connect(self.database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        migrate(self.connection)

    @classmethod
    def from_connection(cls, connection: sqlite3.Connection) -> "FactRepository":
        """Bind repository operations to a caller-owned transaction connection."""
        repository = cls.__new__(cls)
        repository.connection = connection
        repository.connection.row_factory = sqlite3.Row
        repository.connection.execute("PRAGMA foreign_keys = ON")
        database_path = repository.connection.execute("PRAGMA database_list").fetchone()[2]
        repository.database = Path(database_path)
        repository._owns_connection = False
        return repository

    def __enter__(self) -> "FactRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def _canonical_id(self, entity_id: int) -> int:
        repository = EntityRepository(self.database)
        try:
            return repository.resolve_canonical(entity_id)
        finally:
            repository.close()

    def _identity(
        self,
        subject_entity_id: int,
        predicate: str,
        object_value: EntityObject | ScalarObject,
        valid_from: str | None,
        valid_to: str | None,
    ) -> str:
        subject = self._canonical_id(subject_entity_id)
        if isinstance(object_value, EntityObject):
            object_value = EntityObject(self._canonical_id(object_value.entity_id))
        return fact_key(subject, predicate, object_value, valid_from, valid_to)

    def create_candidate(
        self,
        subject_entity_id: int,
        predicate: str,
        object_value: EntityObject | ScalarObject,
        *,
        actor: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        observed_at: str | None = None,
        confidence: float | None = None,
        derivation_id: str | None = None,
    ) -> Fact:
        normalized_predicate = normalize_predicate(predicate)
        key = self._identity(
            subject_entity_id, normalized_predicate, object_value, valid_from, valid_to
        )
        object_type, canonical, normalized_text = canonical_object(object_value)
        object_entity_id = canonical if object_type == "entity" else None
        object_json = None if object_type == "entity" else json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO facts
                   (fact_key, subject_entity_id, predicate, object_entity_id,
                    object_value_json, object_type, object_normalized_text,
                    valid_from, valid_to, observed_at, confidence,
                    creation_derivation_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key, subject_entity_id, normalized_predicate, object_entity_id,
                    object_json, object_type, normalized_text, valid_from, valid_to,
                    observed_at, confidence, derivation_id,
                ),
            )
            fact_id = int(cursor.lastrowid)
            self._event(
                "fact", fact_id, "created", actor, "fact candidate created", None,
                {"review_status": "pending", "knowledge_status": None},
                derivation_id=derivation_id,
            )
        return self.get(fact_id)

    def find_or_create_candidate(
        self,
        subject_entity_id: int,
        predicate: str,
        object_value: EntityObject | ScalarObject,
        *,
        actor: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        **metadata: object,
    ) -> Fact:
        key = self._identity(subject_entity_id, predicate, object_value, valid_from, valid_to)
        row = self.connection.execute(
            """SELECT id FROM facts
               WHERE fact_key=? AND review_status IN ('pending','accepted')""",
            (key,),
        ).fetchone()
        if row is not None:
            return self.get(int(row["id"]))
        return self.create_candidate(
            subject_entity_id, predicate, object_value, actor=actor,
            valid_from=valid_from, valid_to=valid_to, **metadata,
        )

    def get(self, fact_id: int) -> Fact:
        row = self.connection.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        if row is None:
            raise FactNotFoundError(f"fact {fact_id} does not exist")
        value = json.loads(row["object_value_json"]) if row["object_value_json"] is not None else None
        return Fact(
            id=int(row["id"]), fact_key=row["fact_key"],
            subject_entity_id=int(row["subject_entity_id"]), predicate=row["predicate"],
            object_entity_id=row["object_entity_id"], object_value=value,
            object_type=row["object_type"], valid_from=row["valid_from"],
            valid_to=row["valid_to"], observed_at=row["observed_at"],
            confidence=row["confidence"], review_status=row["review_status"],
            knowledge_status=row["knowledge_status"],
        )

    def add_evidence(
        self,
        fact_id: int,
        text_version_id: int,
        *,
        role: str,
        start: int,
        end: int,
        excerpt: str,
        observed_at: str | None = None,
        derivation_id: str | None = None,
        actor: str = "system:fact-repository",
    ) -> Evidence:
        self.get(fact_id)
        version = self.connection.execute(
            """SELECT document_id, normalized_content_hash, normalization_version,
                      plain_content
               FROM document_text_versions WHERE id=?""",
            (text_version_id,),
        ).fetchone()
        if version is None:
            raise LookupError(f"document text version {text_version_id} does not exist")
        validate_span(version["plain_content"], start, end, excerpt)
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO fact_evidence
                   (fact_id, document_text_version_id, document_id,
                    normalized_content_hash, normalization_version, evidence_role,
                    excerpt, start_offset, end_offset, observed_at, derivation_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fact_id, text_version_id, version["document_id"],
                    version["normalized_content_hash"], version["normalization_version"],
                    role, excerpt, start, end, observed_at, derivation_id,
                ),
            )
            evidence_id = int(cursor.lastrowid)
            self._event(
                "fact_evidence", evidence_id, "created", actor,
                "grounded evidence added", None,
                {"fact_id": fact_id, "validation_status": "valid"},
                derivation_id=derivation_id,
            )
        return self._get_evidence(evidence_id)

    def evidence(self, fact_id: int, *, valid_only: bool = False) -> tuple[Evidence, ...]:
        self.get(fact_id)
        sql = "SELECT id FROM fact_evidence WHERE fact_id=?"
        params: list[object] = [fact_id]
        if valid_only:
            sql += " AND validation_status='valid'"
        sql += " ORDER BY id"
        return tuple(self._get_evidence(int(row["id"])) for row in self.connection.execute(sql, params))

    def invalidate_evidence(
        self, evidence_id: int, *, actor: str, reason: str,
    ) -> tuple[int, ...]:
        """Invalidate one Evidence and reopen knowledge that loses its last support."""
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT fact_id, validation_status FROM fact_evidence WHERE id=?",
                (evidence_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"fact evidence {evidence_id} does not exist")
            if row["validation_status"] != "valid":
                self.connection.commit()
                return ()
            self._invalidate_evidence_row(evidence_id, actor=actor, reason=reason)
            fact_ids, _ = self._propagate_invalid_evidence(
                (evidence_id,), actor=actor, reason=reason
            )
            self.connection.commit()
            return fact_ids
        except Exception:
            self.connection.rollback()
            raise

    def invalidate_text_version(
        self, version_id: int, limit: int, *, actor: str, reason: str,
    ) -> InvalidationResult:
        """Invalidate at most ``limit`` stale Mention/Evidence rows for one text version."""
        if limit <= 0:
            raise ValueError("invalidation limit must be positive")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            version = self.connection.execute(
                "SELECT id FROM document_text_versions WHERE id=?", (version_id,)
            ).fetchone()
            if version is None:
                raise LookupError(f"document text version {version_id} does not exist")
            items = self.connection.execute(
                """SELECT kind, id FROM (
                       SELECT 'mention' AS kind, id FROM entity_mentions
                       WHERE document_text_version_id=? AND invalidated_at IS NULL
                       UNION ALL
                       SELECT 'evidence' AS kind, id FROM fact_evidence
                       WHERE document_text_version_id=? AND validation_status='valid'
                   ) ORDER BY kind DESC, id LIMIT ?""",
                (version_id, version_id, limit),
            ).fetchall()
            mention_ids = tuple(int(row["id"]) for row in items if row["kind"] == "mention")
            evidence_ids = tuple(int(row["id"]) for row in items if row["kind"] == "evidence")
            entity_ids: tuple[int, ...] = ()
            if mention_ids:
                placeholders = ",".join("?" for _ in mention_ids)
                entity_ids = tuple(
                    int(row["entity_id"])
                    for row in self.connection.execute(
                        f"SELECT DISTINCT entity_id FROM entity_mentions WHERE id IN ({placeholders}) AND entity_id IS NOT NULL ORDER BY entity_id",
                        mention_ids,
                    )
                )
                self.connection.executemany(
                    """UPDATE entity_mentions SET invalidated_at=CURRENT_TIMESTAMP,
                              invalidation_reason=?, updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND invalidated_at IS NULL""",
                    ((reason, mention_id) for mention_id in mention_ids),
                )
                for mention_id in mention_ids:
                    self._event(
                        "entity_mention", mention_id, "invalidated", actor, reason,
                        {"invalidated": False}, {"invalidated": True},
                    )
            for evidence_id in evidence_ids:
                self._invalidate_evidence_row(evidence_id, actor=actor, reason=reason)
            fact_ids, relation_ids = self._propagate_invalid_evidence(
                evidence_ids, actor=actor, reason=reason
            )
            remaining = self.connection.execute(
                """SELECT EXISTS(
                       SELECT 1 FROM entity_mentions
                       WHERE document_text_version_id=? AND invalidated_at IS NULL
                       UNION ALL
                       SELECT 1 FROM fact_evidence
                       WHERE document_text_version_id=? AND validation_status='valid'
                   )""",
                (version_id, version_id),
            ).fetchone()[0]
            if not remaining:
                self.connection.execute(
                    """UPDATE document_text_versions
                       SET invalidated_at=COALESCE(invalidated_at, CURRENT_TIMESTAMP),
                           invalidation_reason=COALESCE(invalidation_reason, ?)
                       WHERE id=?""",
                    (reason, version_id),
                )
            self.connection.commit()
            return InvalidationResult(
                mention_ids, evidence_ids, entity_ids, fact_ids, relation_ids
            )
        except Exception:
            self.connection.rollback()
            raise

    def _invalidate_evidence_row(self, evidence_id: int, *, actor: str, reason: str) -> None:
        updated = self.connection.execute(
            """UPDATE fact_evidence SET validation_status='stale',
                      invalidated_at=CURRENT_TIMESTAMP, invalidation_reason=?
               WHERE id=? AND validation_status='valid'""",
            (reason, evidence_id),
        )
        if updated.rowcount:
            self._event(
                "fact_evidence", evidence_id, "invalidated", actor, reason,
                {"validation_status": "valid"}, {"validation_status": "stale"},
            )

    def _propagate_invalid_evidence(
        self, evidence_ids: tuple[int, ...], *, actor: str, reason: str,
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        if not evidence_ids:
            return (), ()
        placeholders = ",".join("?" for _ in evidence_ids)
        fact_ids = tuple(
            int(row["id"])
            for row in self.connection.execute(
                f"""SELECT DISTINCT fact.id FROM facts AS fact
                     JOIN fact_evidence AS evidence ON evidence.fact_id=fact.id
                     WHERE evidence.id IN ({placeholders})
                       AND fact.review_status='accepted'
                       AND NOT EXISTS (
                           SELECT 1 FROM fact_evidence AS valid
                           WHERE valid.fact_id=fact.id AND valid.validation_status='valid'
                       ) ORDER BY fact.id""",
                evidence_ids,
            )
        )
        relation_ids = tuple(
            int(row["id"])
            for row in self.connection.execute(
                f"""SELECT DISTINCT relation.id FROM fact_relations AS relation
                     JOIN fact_relation_evidence AS link ON link.fact_relation_id=relation.id
                     WHERE link.fact_evidence_id IN ({placeholders})
                       AND relation.review_status='accepted'
                       AND NOT EXISTS (
                           SELECT 1 FROM fact_relation_evidence AS remaining_link
                           JOIN fact_evidence AS remaining
                             ON remaining.id=remaining_link.fact_evidence_id
                           WHERE remaining_link.fact_relation_id=relation.id
                             AND remaining.validation_status='valid'
                       ) ORDER BY relation.id""",
                evidence_ids,
            )
        )
        for relation_id in relation_ids:
            relation = self.get_relation(relation_id)
            self.connection.execute(
                """UPDATE fact_relations SET review_status='pending', knowledge_status=NULL,
                          updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (relation_id,),
            )
            self._event(
                "fact_relation", relation_id, "reopened", actor, reason,
                {"review_status": "accepted", "knowledge_status": relation.knowledge_status},
                {"review_status": "pending", "knowledge_status": None},
            )
        for fact_id in fact_ids:
            fact = self.get(fact_id)
            self.connection.execute(
                """UPDATE facts SET review_status='pending', knowledge_status=NULL,
                          updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (fact_id,),
            )
            self._event(
                "fact", fact_id, "reopened", actor, reason,
                {"review_status": "accepted", "knowledge_status": fact.knowledge_status},
                {"review_status": "pending", "knowledge_status": None},
            )
        return fact_ids, relation_ids

    def _get_evidence(self, evidence_id: int) -> Evidence:
        row = self.connection.execute(
            "SELECT * FROM fact_evidence WHERE id=?", (evidence_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"fact evidence {evidence_id} does not exist")
        return Evidence(
            id=int(row["id"]), fact_id=int(row["fact_id"]),
            document_text_version_id=int(row["document_text_version_id"]),
            document_id=int(row["document_id"]), role=row["evidence_role"],
            excerpt=row["excerpt"], start_offset=int(row["start_offset"]),
            end_offset=int(row["end_offset"]), observed_at=row["observed_at"],
            validation_status=row["validation_status"],
        )

    def get_relation(self, relation_id: int) -> FactRelation:
        row = self.connection.execute(
            "SELECT * FROM fact_relations WHERE id=?", (relation_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"fact relation {relation_id} does not exist")
        return FactRelation(
            id=int(row["id"]), left_fact_id=int(row["left_fact_id"]),
            right_fact_id=int(row["right_fact_id"]), relation_type=row["relation_type"],
            confidence=row["confidence"], explanation=row["explanation"],
            deterministic_validation_status=row["deterministic_validation_status"],
            review_status=row["review_status"], knowledge_status=row["knowledge_status"],
        )

    @staticmethod
    def _require_human(actor: str) -> None:
        if _actor_type(actor) != "human":
            raise FactRelationDecisionError("relation decision requires a human actor")

    def create_relation_candidate(
        self,
        left_fact_id: int,
        right_fact_id: int,
        relation_type: str,
        *,
        explanation: str,
        evidence_ids: list[int] | tuple[int, ...],
        actor: str,
        confidence: float | None = None,
        derivation_id: str | None = None,
    ) -> FactRelation:
        if relation_type in {"conflicts_with", "corroborates"}:
            left_fact_id, right_fact_id = sorted((left_fact_id, right_fact_id))
        if left_fact_id == right_fact_id:
            raise FactRelationDecisionError("relation endpoints must differ")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            endpoints = {
                int(row["id"]): row
                for row in self.connection.execute(
                    "SELECT id, review_status FROM facts WHERE id IN (?, ?)",
                    (left_fact_id, right_fact_id),
                )
            }
            if set(endpoints) != {left_fact_id, right_fact_id} or any(
                row["review_status"] != "accepted" for row in endpoints.values()
            ):
                raise FactRelationDecisionError("relation endpoints must be accepted facts")
            if not evidence_ids:
                raise FactRelationDecisionError("relation requires endpoint evidence")
            placeholders = ",".join("?" for _ in evidence_ids)
            evidence_rows = self.connection.execute(
                f"SELECT id, fact_id, validation_status FROM fact_evidence WHERE id IN ({placeholders})",
                tuple(evidence_ids),
            ).fetchall()
            if len(evidence_rows) != len(set(evidence_ids)):
                raise FactRelationDecisionError("relation evidence does not exist")
            if any(int(row["fact_id"]) not in endpoints for row in evidence_rows):
                raise FactRelationDecisionError("relation evidence must belong to an endpoint")
            if not any(row["validation_status"] == "valid" for row in evidence_rows):
                raise FactRelationDecisionError("relation requires valid endpoint evidence")
            cursor = self.connection.execute(
                """INSERT INTO fact_relations
                   (left_fact_id, right_fact_id, relation_type, confidence, explanation,
                    deterministic_validation_status, derivation_id)
                   VALUES (?, ?, ?, ?, ?, 'passed', ?)""",
                (left_fact_id, right_fact_id, relation_type, confidence, explanation, derivation_id),
            )
            relation_id = int(cursor.lastrowid)
            self.connection.executemany(
                """INSERT INTO fact_relation_evidence
                   (fact_relation_id, fact_evidence_id, evidence_role)
                   VALUES (?, ?, 'supports_relation')""",
                ((relation_id, int(row["id"])) for row in evidence_rows),
            )
            self._event(
                "fact_relation", relation_id, "validated", actor, explanation, None,
                {"review_status": "pending", "deterministic_validation_status": "passed"},
                derivation_id=derivation_id,
            )
            if relation_type == "conflicts_with":
                for fact_id in (left_fact_id, right_fact_id):
                    previous = self.get(fact_id)
                    if previous.knowledge_status == "active":
                        self.connection.execute(
                            "UPDATE facts SET knowledge_status='disputed', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (fact_id,),
                        )
                        self._event(
                            "fact", fact_id, "disputed", actor,
                            f"validated conflict relation {relation_id}",
                            {"review_status": "accepted", "knowledge_status": "active"},
                            {"review_status": "accepted", "knowledge_status": "disputed"},
                        )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_relation(relation_id)

    def _supersedes_would_cycle(self, left_fact_id: int, right_fact_id: int) -> bool:
        return self.connection.execute(
            """WITH RECURSIVE reachable(id) AS (
                   SELECT ?
                   UNION
                   SELECT relation.right_fact_id
                   FROM fact_relations AS relation JOIN reachable
                     ON relation.left_fact_id = reachable.id
                   WHERE relation.relation_type='supersedes'
                     AND relation.review_status='accepted'
               ) SELECT EXISTS(SELECT 1 FROM reachable WHERE id=?)""",
            (right_fact_id, left_fact_id),
        ).fetchone()[0] == 1

    def accept_relation(self, relation_id: int, *, actor: str, reason: str) -> FactRelation:
        self._require_human(actor)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            relation = self.get_relation(relation_id)
            if relation.review_status != "pending" or relation.deterministic_validation_status != "passed":
                raise FactRelationDecisionError("only validated pending relations can be accepted")
            if relation.relation_type == "supersedes" and self._supersedes_would_cycle(
                relation.left_fact_id, relation.right_fact_id
            ):
                raise FactRelationCycleError("supersedes relation would create a cycle")
            self.connection.execute(
                """UPDATE fact_relations SET review_status='accepted', knowledge_status='active',
                          reviewed_at=CURRENT_TIMESTAMP, reviewed_by=?, review_reason=?,
                          updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (actor, reason, relation_id),
            )
            self._event(
                "fact_relation", relation_id, "accepted", actor, reason,
                {"review_status": "pending", "knowledge_status": None},
                {"review_status": "accepted", "knowledge_status": "active"},
            )
            if relation.relation_type == "supersedes":
                older = self.get(relation.right_fact_id)
                self.connection.execute(
                    "UPDATE facts SET knowledge_status='superseded', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (older.id,),
                )
                self._event(
                    "fact", older.id, "superseded", actor, reason,
                    {"review_status": "accepted", "knowledge_status": older.knowledge_status},
                    {"review_status": "accepted", "knowledge_status": "superseded"},
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_relation(relation_id)

    def reject_relation(self, relation_id: int, *, actor: str, reason: str) -> FactRelation:
        self._require_human(actor)
        relation = self.get_relation(relation_id)
        if relation.review_status != "pending":
            raise FactRelationDecisionError("only pending relations can be rejected")
        with self.connection:
            self.connection.execute(
                """UPDATE fact_relations SET review_status='rejected', reviewed_at=CURRENT_TIMESTAMP,
                          reviewed_by=?, review_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (actor, reason, relation_id),
            )
            self._event(
                "fact_relation", relation_id, "rejected", actor, reason,
                {"review_status": "pending", "knowledge_status": None},
                {"review_status": "rejected", "knowledge_status": None},
            )
        return self.get_relation(relation_id)

    def resolve_dispute(self, fact_ids: list[int] | tuple[int, ...], *, actor: str, reason: str) -> None:
        self._require_human(actor)
        unique_ids = tuple(dict.fromkeys(fact_ids))
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            for fact_id in unique_ids:
                fact = self.get(fact_id)
                remaining = self.connection.execute(
                    """SELECT 1 FROM fact_relations
                       WHERE relation_type='conflicts_with'
                         AND deterministic_validation_status='passed'
                         AND review_status IN ('pending','accepted')
                         AND (left_fact_id=? OR right_fact_id=?) LIMIT 1""",
                    (fact_id, fact_id),
                ).fetchone()
                if remaining is not None:
                    raise FactRelationDecisionError("fact still has a validated conflict")
                if fact.knowledge_status == "disputed":
                    self.connection.execute(
                        "UPDATE facts SET knowledge_status='active', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (fact_id,),
                    )
                    self._event(
                        "fact", fact_id, "accepted", actor, reason,
                        {"review_status": "accepted", "knowledge_status": "disputed"},
                        {"review_status": "accepted", "knowledge_status": "active"},
                    )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def supersede(
        self, newer_fact_id: int, older_fact_id: int, *, explanation: str,
        evidence_ids: list[int] | tuple[int, ...], actor: str, reason: str,
    ) -> FactRelation:
        self._require_human(actor)
        relation = self.create_relation_candidate(
            newer_fact_id, older_fact_id, "supersedes", explanation=explanation,
            evidence_ids=evidence_ids, actor=actor,
        )
        return self.accept_relation(relation.id, actor=actor, reason=reason)

    def retract(self, fact_id: int, *, actor: str, reason: str) -> Fact:
        self._require_human(actor)
        fact = self.get(fact_id)
        if fact.review_status != "accepted" or fact.knowledge_status not in {"active", "disputed"}:
            raise InvalidFactTransition("only current accepted facts can be retracted")
        with self.connection:
            self.connection.execute(
                "UPDATE facts SET knowledge_status='retracted', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (fact_id,),
            )
            self._event(
                "fact", fact_id, "retracted", actor, reason,
                {"review_status": "accepted", "knowledge_status": fact.knowledge_status},
                {"review_status": "accepted", "knowledge_status": "retracted"},
            )
        return self.get(fact_id)

    def accept(self, fact_id: int, *, actor: str, reason: str) -> Fact:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            result = self.accept_in_transaction(fact_id, actor=actor, reason=reason)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return result

    def accept_in_transaction(self, fact_id: int, *, actor: str, reason: str) -> Fact:
        """Accept a Fact without managing the caller-owned transaction."""
        if not self.connection.in_transaction:
            raise RuntimeError("accept_in_transaction requires an active transaction")
        current = self.get(fact_id)
        if current.review_status != "pending":
            raise InvalidFactTransition(
                f"cannot accept fact {fact_id} from {current.review_status}"
            )
        if not self.evidence(fact_id, valid_only=True):
            raise FactPublicationError("fact publication requires valid evidence")
        endpoint_ids = [current.subject_entity_id]
        if current.object_entity_id is not None:
            endpoint_ids.append(current.object_entity_id)
        placeholders = ",".join("?" for _ in endpoint_ids)
        accepted_count = self.connection.execute(
            f"SELECT count(*) FROM entities WHERE id IN ({placeholders}) "
            "AND review_status='accepted'",
            endpoint_ids,
        ).fetchone()[0]
        if accepted_count != len(set(endpoint_ids)):
            raise FactPublicationError("fact publication requires accepted entities")
        updated = self.connection.execute(
            """UPDATE facts SET review_status='accepted', knowledge_status='active',
                      reviewed_at=CURRENT_TIMESTAMP, reviewed_by=?, review_reason=?,
                      updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND review_status='pending'""",
            (actor, reason, fact_id),
        )
        if updated.rowcount != 1:
            raise InvalidFactTransition("fact state changed concurrently")
        self._event(
            "fact", fact_id, "accepted", actor, reason,
            {"review_status": "pending", "knowledge_status": None},
            {"review_status": "accepted", "knowledge_status": "active"},
        )
        return self.get(fact_id)

    def reject(self, fact_id: int, *, actor: str, reason: str) -> Fact:
        return self._transition_pending(fact_id, "rejected", actor, reason)

    def reopen(self, fact_id: int, *, actor: str, reason: str) -> Fact:
        current = self.get(fact_id)
        if current.review_status != "accepted":
            raise InvalidFactTransition(
                f"cannot reopen fact {fact_id} from {current.review_status}"
            )
        with self.connection:
            self.connection.execute(
                """UPDATE facts SET review_status='pending', knowledge_status=NULL,
                          reviewed_at=CURRENT_TIMESTAMP, reviewed_by=?, review_reason=?,
                          updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (actor, reason, fact_id),
            )
            self._event(
                "fact", fact_id, "reopened", actor, reason,
                {"review_status": "accepted", "knowledge_status": current.knowledge_status},
                {"review_status": "pending", "knowledge_status": None},
            )
        return self.get(fact_id)

    def _transition_pending(self, fact_id: int, target: str, actor: str, reason: str) -> Fact:
        current = self.get(fact_id)
        if current.review_status != "pending":
            raise InvalidFactTransition(
                f"cannot {target} fact {fact_id} from {current.review_status}"
            )
        with self.connection:
            self.connection.execute(
                """UPDATE facts SET review_status=?, reviewed_at=CURRENT_TIMESTAMP,
                          reviewed_by=?, review_reason=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND review_status='pending'""",
                (target, actor, reason, fact_id),
            )
            self._event(
                "fact", fact_id, target, actor, reason,
                {"review_status": "pending", "knowledge_status": None},
                {"review_status": target, "knowledge_status": None},
            )
        return self.get(fact_id)

    def _event(
        self, object_type: str, object_id: int, event_type: str, actor: str,
        reason: str, previous: dict[str, object] | None,
        new: dict[str, object] | None, *, derivation_id: str | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO knowledge_events
               (object_type, object_id, event_type, actor_type, actor_id, reason,
                derivation_id, previous_state_json, new_state_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                object_type, object_id, event_type, _actor_type(actor), actor, reason,
                derivation_id,
                json.dumps(previous, ensure_ascii=False, sort_keys=True) if previous is not None else None,
                json.dumps(new, ensure_ascii=False, sort_keys=True) if new is not None else None,
            ),
        )


__all__ = [
    "FactRepository", "FactPublicationError", "InvalidFactTransition", "FactNotFoundError",
    "FactRelationDecisionError", "FactRelationCycleError", "InvalidationResult",
]
