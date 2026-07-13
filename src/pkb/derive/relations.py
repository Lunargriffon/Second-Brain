"""Bounded, deterministic relation-candidate discovery.

This module only discovers candidate pairs.  It never calls a derivation provider
and does not write relations.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pkb.knowledge.fingerprint import derivation_input_hash
from pkb.knowledge.migrations import migrate
from pkb.knowledge.search import SearchIndex


MAX_HEURISTIC_LIMIT = 100
MAX_PER_DOCUMENT_LIMIT = 200
MAX_DOCUMENT_LIMIT = 10_000
MAX_PAIR_LIMIT = 100_000
CONFIDENT_LABEL = 0.7


@dataclass(frozen=True)
class Candidate:
    document_id: int
    score: float
    evidence_sources: tuple[str, ...]


@dataclass(frozen=True)
class CandidatePair:
    source_document_id: int
    target_document_id: int
    score: float
    evidence_sources: tuple[str, ...]


@dataclass(frozen=True)
class RelationDryRun:
    documents_considered: int
    pairs: tuple[CandidatePair, ...]

    @property
    def pair_count(self) -> int:
        return len(self.pairs)


class RelationCandidateBuilder:
    """Union independently bounded heuristics into stable candidate lists."""

    def __init__(self, database: str | Path, *, heuristic_limit: int = 20) -> None:
        _bounded("heuristic_limit", heuristic_limit, MAX_HEURISTIC_LIMIT)
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.create_function(
            "derivation_input_hash", 3, derivation_input_hash, deterministic=True
        )
        migrate(self.connection)
        self.heuristic_limit = heuristic_limit

    def __enter__(self) -> "RelationCandidateBuilder":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def for_document(
        self, document_id: int, *, per_document_limit: int = 50
    ) -> tuple[Candidate, ...]:
        _bounded("per_document_limit", per_document_limit, MAX_PER_DOCUMENT_LIMIT)
        if self.connection.execute(
            "SELECT 1 FROM documents WHERE id=?", (document_id,)
        ).fetchone() is None:
            raise KeyError(f"document {document_id} does not exist")

        scores: dict[int, float] = {}
        evidence: dict[int, set[str]] = {}

        for kind in ("tag", "topic"):
            weight = 2.0 if kind == "tag" else 2.5
            for candidate_id, confidence in self._shared_labels(document_id, kind):
                self._admit(
                    scores,
                    evidence,
                    candidate_id,
                    f"shared_{kind}",
                    weight * confidence,
                )

        for source, seed, weight in self._fts_seeds(document_id):
            for rank, candidate_id in enumerate(self._fts_neighbors(document_id, seed), start=1):
                self._admit(scores, evidence, candidate_id, source, weight / rank)

        for candidate_id, distance_days in self._time_neighbors(document_id):
            proximity = 0.8 / (1.0 + distance_days / 30.0)
            self._admit(
                scores,
                evidence,
                candidate_id,
                "same_collection_time",
                proximity,
            )

        candidates = tuple(
            Candidate(
                document_id=candidate_id,
                score=round(score, 8),
                evidence_sources=tuple(sorted(evidence[candidate_id])),
            )
            for candidate_id, score in scores.items()
            if candidate_id != document_id
        )
        return tuple(
            sorted(candidates, key=lambda item: (-item.score, item.document_id))[
                :per_document_limit
            ]
        )

    def dry_run(
        self,
        *,
        document_limit: int,
        per_document_limit: int = 50,
        pair_limit: int,
    ) -> RelationDryRun:
        """Return a bounded corpus preview without invoking any provider."""
        _bounded("document_limit", document_limit, MAX_DOCUMENT_LIMIT)
        _bounded("per_document_limit", per_document_limit, MAX_PER_DOCUMENT_LIMIT)
        _bounded("pair_limit", pair_limit, MAX_PAIR_LIMIT)
        document_ids = tuple(
            int(row[0])
            for row in self.connection.execute(
                "SELECT id FROM documents ORDER BY id LIMIT ?", (document_limit,)
            )
        )
        pair_scores: dict[tuple[int, int], float] = {}
        pair_evidence: dict[tuple[int, int], set[str]] = {}
        for document_id in document_ids:
            for candidate in self.for_document(
                document_id, per_document_limit=per_document_limit
            ):
                key = tuple(sorted((document_id, candidate.document_id)))
                pair_scores[key] = max(pair_scores.get(key, candidate.score), candidate.score)
                pair_evidence.setdefault(key, set()).update(candidate.evidence_sources)
        pairs = (
            CandidatePair(
                source_document_id=key[0],
                target_document_id=key[1],
                score=score,
                evidence_sources=tuple(sorted(pair_evidence[key])),
            )
            for key, score in pair_scores.items()
        )
        selected = tuple(
            sorted(
                pairs,
                key=lambda pair: (
                    -pair.score,
                    pair.source_document_id,
                    pair.target_document_id,
                ),
            )[:pair_limit]
        )
        return RelationDryRun(len(document_ids), selected)

    @staticmethod
    def _admit(
        scores: dict[int, float],
        evidence: dict[int, set[str]],
        document_id: int,
        source: str,
        score: float,
    ) -> None:
        scores[document_id] = scores.get(document_id, 0.0) + score
        evidence.setdefault(document_id, set()).add(source)

    def _shared_labels(self, document_id: int, kind: str) -> tuple[tuple[int, float], ...]:
        table = "tags" if kind == "tag" else "topics"
        label_column = "tag_id" if kind == "tag" else "topic_id"
        join_table = "document_tags" if kind == "tag" else "document_topics"
        try:
            rows = self.connection.execute(
                f"""SELECT candidate.document_id,
                           MAX(MIN(COALESCE(source.confidence, 1.0),
                                   COALESCE(candidate.confidence, 1.0))) AS confidence
                    FROM {join_table} AS source
                    JOIN {join_table} AS candidate
                      ON candidate.{label_column}=source.{label_column}
                     AND candidate.document_id<>source.document_id
                    JOIN {table} AS label ON label.id=source.{label_column}
                    WHERE source.document_id=?
                      AND (source.origin='user' OR (
                           source.origin='ai' AND source.confidence>=?
                           AND EXISTS (
                               SELECT 1 FROM derivations AS source_derivation
                               JOIN documents AS source_document
                                 ON source_document.id=source.document_id
                               WHERE source_derivation.id=source.derivation_id
                                 AND source_derivation.document_id=source.document_id
                                 AND source_derivation.kind='article'
                                 AND source_derivation.status='accepted'
                                 AND source_derivation.input_hash=derivation_input_hash(
                                     source_document.source_content_hash,
                                     source_document.normalized_content_hash,
                                     source_document.normalization_version)
                           )))
                      AND (candidate.origin='user' OR (
                           candidate.origin='ai' AND candidate.confidence>=?
                           AND EXISTS (
                               SELECT 1 FROM derivations AS candidate_derivation
                               JOIN documents AS candidate_document
                                 ON candidate_document.id=candidate.document_id
                               WHERE candidate_derivation.id=candidate.derivation_id
                                 AND candidate_derivation.document_id=candidate.document_id
                                 AND candidate_derivation.kind='article'
                                 AND candidate_derivation.status='accepted'
                                 AND candidate_derivation.input_hash=derivation_input_hash(
                                     candidate_document.source_content_hash,
                                     candidate_document.normalized_content_hash,
                                     candidate_document.normalization_version)
                           )))
                    GROUP BY candidate.document_id
                    ORDER BY confidence DESC, candidate.document_id ASC
                    LIMIT ?""",
                (document_id, CONFIDENT_LABEL, CONFIDENT_LABEL, self.heuristic_limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return ()
        return tuple((int(row[0]), float(row[1])) for row in rows)

    def _fts_seeds(self, document_id: int) -> tuple[tuple[str, str, float], ...]:
        try:
            row = self.connection.execute(
                """SELECT title_terms
                   FROM documents_search_content WHERE document_id=? LIMIT 1""",
                (document_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return ()
        if row is None:
            return ()
        seeds: list[tuple[str, str, float]] = []
        if row["title_terms"].strip():
            seeds.append(("fts_title", row["title_terms"], 1.5))

        try:
            derivation = self.connection.execute(
                """SELECT derivation.payload_json FROM derivations AS derivation
                   JOIN documents AS document ON document.id=derivation.document_id
                   WHERE derivation.document_id=? AND derivation.kind='article'
                     AND derivation.status='accepted'
                     AND derivation.input_hash=derivation_input_hash(
                         document.source_content_hash, document.normalized_content_hash,
                         document.normalization_version)
                   ORDER BY derivation.created_at DESC, derivation.id DESC LIMIT 1""",
                (document_id,),
            ).fetchone()
            payload = json.loads(derivation[0]) if derivation else {}
            summary = payload.get("summary", "") if isinstance(payload, dict) else ""
            if isinstance(summary, str):
                terms = SearchIndex._terms(summary)
                if terms:
                    seeds.append(("fts_summary", terms, 1.2))
            key_points = payload.get("key_points", []) if isinstance(payload, dict) else []
            if isinstance(key_points, list):
                terms = SearchIndex._terms(
                    " ".join(item for item in key_points if isinstance(item, str))
                )
                if terms:
                    seeds.append(("fts_key_point", terms, 1.0))
        except (json.JSONDecodeError, sqlite3.OperationalError):
            pass
        return tuple(seeds)

    def _fts_neighbors(self, document_id: int, terms: str) -> tuple[int, ...]:
        tokens = tuple(dict.fromkeys(terms.split()))
        if not tokens:
            return ()
        match = " OR ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens
        )
        try:
            rows = self.connection.execute(
                """SELECT candidate.document_id
                   FROM documents_fts
                   JOIN documents_search_content AS candidate
                     ON candidate.document_id=documents_fts.rowid
                   WHERE documents_fts MATCH ? AND candidate.document_id<>?
                   ORDER BY bm25(documents_fts, 10.0, 1.0, 3.0, 5.0) ASC,
                            candidate.document_id ASC
                   LIMIT ?""",
                (match, document_id, self.heuristic_limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return ()
        return tuple(int(row[0]) for row in rows)

    def _time_neighbors(self, document_id: int) -> tuple[tuple[int, float], ...]:
        try:
            memberships = self.connection.execute(
                """SELECT source, collection_id, observed_at
                   FROM source_memberships
                   WHERE document_id=? AND collection_id<>'' AND observed_at IS NOT NULL
                   ORDER BY id LIMIT ?""",
                (document_id, self.heuristic_limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return ()
        nearest: dict[int, float] = {}
        for membership in memberships:
            for comparison, direction in (("<", "DESC"), (">=", "ASC")):
                try:
                    rows = self.connection.execute(
                        f"""SELECT candidate.document_id,
                                   ABS(julianday(candidate.observed_at)-julianday(?)) AS distance_days
                            FROM source_memberships AS candidate
                            WHERE candidate.source=? AND candidate.collection_id=?
                              AND candidate.observed_at{comparison}?
                              AND candidate.document_id<>?
                            ORDER BY candidate.observed_at {direction}, candidate.document_id ASC
                            LIMIT ?""",
                        (
                            membership["observed_at"],
                            membership["source"],
                            membership["collection_id"],
                            membership["observed_at"],
                            document_id,
                            self.heuristic_limit,
                        ),
                    ).fetchall()
                except sqlite3.OperationalError:
                    continue
                for row in rows:
                    if row[1] is None:
                        continue
                    candidate_id, distance = int(row[0]), float(row[1])
                    nearest[candidate_id] = min(nearest.get(candidate_id, distance), distance)
        return tuple(
            sorted(nearest.items(), key=lambda item: (item[1], item[0]))[
                : self.heuristic_limit
            ]
        )


def _bounded(name: str, value: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
