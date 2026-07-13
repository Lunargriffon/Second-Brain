"""Bounded, deterministic relation-candidate discovery.

This module only discovers candidate pairs.  It never calls a derivation provider
and does not write relations.
"""

from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from pkb.derive.prompts import RELATION_PROMPT_VERSION, RELATION_SYSTEM_PROMPT
from pkb.derive.provider import (
    DerivationProvider,
    ProviderAuthError,
    ProviderInvalidRequestError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from pkb.knowledge.fingerprint import derivation_input_hash
from pkb.knowledge.migrations import migrate
from pkb.knowledge.search import SearchIndex


MAX_HEURISTIC_LIMIT = 100
MAX_PER_DOCUMENT_LIMIT = 200
MAX_DOCUMENT_LIMIT = 10_000
MAX_PAIR_LIMIT = 100_000
CONFIDENT_LABEL = 0.7
RELATION_SCHEMA_VERSION = 1
RELATION_TYPES = frozenset(
    {"supports", "contrasts", "extends", "example_of", "similar_to"}
)


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


@dataclass(frozen=True)
class ValidatedRelation:
    relation_type: str
    score: float
    evidence: tuple[tuple[str, str], ...]
    explanation: str


@dataclass(frozen=True)
class RelationPipelineResult:
    processed: int = 0
    accepted: int = 0
    no_relation: int = 0
    invalid: int = 0
    failed: int = 0
    stopped: bool = False


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def validate_relation(
    payload: Mapping[str, object],
    *,
    source_text: str,
    target_text: str,
    source_stable_id: str,
    target_stable_id: str,
) -> ValidatedRelation:
    """Validate strict provider output and source-ground every relation."""
    required = {"type", "score", "evidence", "explanation"}
    if set(payload) != required:
        raise ValueError("relation fields must exactly match the schema")
    relation_type = payload["type"]
    score = payload["score"]
    evidence = payload["evidence"]
    explanation = payload["explanation"]
    if not isinstance(relation_type, str) or relation_type not in RELATION_TYPES | {"no_relation"}:
        raise ValueError("invalid relation type")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("relation score must be a number")
    numeric_score = float(score)
    if not math.isfinite(numeric_score) or not 0 <= numeric_score <= 1:
        raise ValueError("relation score must be finite and between 0 and 1")
    if not isinstance(evidence, list):
        raise ValueError("relation evidence must be an array")
    source_by_id = {
        source_stable_id: _normalize_whitespace(source_text),
        target_stable_id: _normalize_whitespace(target_text),
    }
    normalized_evidence: list[tuple[str, str]] = []
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"document_id", "excerpt"}:
            raise ValueError("relation evidence item fields must exactly match the schema")
        document_id, excerpt = item["document_id"], item["excerpt"]
        if not isinstance(document_id, str) or document_id not in source_by_id:
            raise ValueError("relation evidence references an unknown stable document ID")
        if not isinstance(excerpt, str) or not (normalized_excerpt := _normalize_whitespace(excerpt)):
            raise ValueError("relation evidence excerpt must be non-empty")
        if normalized_excerpt not in source_by_id[document_id]:
            raise ValueError("relation evidence is not present in its identified source")
        normalized_evidence.append((document_id, normalized_excerpt))
    if relation_type == "no_relation":
        if normalized_evidence:
            raise ValueError("no_relation cannot contain evidence")
    elif not normalized_evidence:
        raise ValueError("relation evidence must be non-empty")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("relation explanation must be non-empty")
    if source_stable_id not in explanation or target_stable_id not in explanation:
        raise ValueError("relation explanation must reference both stable document IDs")
    return ValidatedRelation(
        relation_type, numeric_score, tuple(normalized_evidence), _normalize_whitespace(explanation)
    )


class RelationPipeline:
    """Derive only the bounded candidate union and maintain its current projection.

    Accepted derivations are immutable audit records.  The ``relations`` table is
    deliberately a current projection: a later accepted decision transactionally
    replaces the projected edge for that canonical pair, while rejected provider
    output leaves the existing projection untouched.
    """

    def __init__(
        self,
        database: str | Path,
        provider: DerivationProvider,
        *,
        run_log: str | Path,
        prompt_version: str = RELATION_PROMPT_VERSION,
        retry_delays: tuple[float, ...] = (1.0, 4.0),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not prompt_version.strip():
            raise ValueError("prompt_version must be non-empty")
        if any(delay < 0 for delay in retry_delays):
            raise ValueError("retry delays cannot be negative")
        self.database = Path(database)
        self.provider = provider
        self.run_log = Path(run_log)
        self.prompt_version = prompt_version
        self.retry_delays = retry_delays
        self.sleeper = sleeper

    def run(
        self,
        *,
        document_limit: int,
        per_document_limit: int,
        pair_limit: int,
    ) -> RelationPipelineResult:
        processed = accepted = no_relation = invalid = failed = 0
        stopped = False
        with RelationCandidateBuilder(self.database) as builder:
            preview = builder.dry_run(
                document_limit=document_limit,
                per_document_limit=per_document_limit,
                pair_limit=pair_limit,
            )
            for pair in preview.pairs:
                processed += 1
                source, target = self._documents(builder.connection, pair)
                input_hash = self._pair_fingerprint(source, target, pair.evidence_sources)
                derivation_id = hashlib.sha256(
                    f"relation\0{input_hash}".encode("utf-8")
                ).hexdigest()
                existing = builder.connection.execute(
                    "SELECT payload_json FROM derivations WHERE id=? AND status='accepted'",
                    (derivation_id,),
                ).fetchone()
                if existing is not None:
                    payload = json.loads(existing[0])
                    with builder.connection:
                        self._reconcile(
                            builder.connection, pair, derivation_id, payload, promote=False
                        )
                    if payload["type"] == "no_relation":
                        no_relation += 1
                    else:
                        accepted += 1
                    continue
                try:
                    response = self._complete_with_retry(source, target)
                    relation = validate_relation(
                        response,
                        source_text=source["plain_content"] or "",
                        target_text=target["plain_content"] or "",
                        source_stable_id=source["identity_key"],
                        target_stable_id=target["identity_key"],
                    )
                    payload = self._payload(relation, pair)
                    self._store(
                        builder.connection,
                        source,
                        target,
                        pair,
                        input_hash,
                        derivation_id,
                        payload,
                    )
                    if relation.relation_type == "no_relation":
                        no_relation += 1
                        self._append_log(pair, derivation_id, "no_relation")
                    else:
                        accepted += 1
                        self._append_log(pair, derivation_id, "accepted")
                except (ValueError, ProviderMalformedResponseError):
                    invalid += 1
                    self._append_log(pair, None, "rejected", "invalid_relation")
                except (ProviderAuthError, ProviderInvalidRequestError):
                    failed += 1
                    stopped = True
                    self._append_log(pair, None, "failed", "provider_permanent")
                    break
                except (ProviderRateLimitError, ProviderTemporaryError):
                    failed += 1
                    stopped = True
                    self._append_log(pair, None, "failed", "provider_temporary")
                    break
        return RelationPipelineResult(
            processed, accepted, no_relation, invalid, failed, stopped
        )

    @staticmethod
    def _documents(connection: sqlite3.Connection, pair: CandidatePair):
        rows = connection.execute(
            "SELECT * FROM documents WHERE id IN (?, ?) ORDER BY id",
            (pair.source_document_id, pair.target_document_id),
        ).fetchall()
        if len(rows) != 2:
            raise KeyError("relation candidate document is missing")
        return rows[0], rows[1]

    def _pair_fingerprint(
        self, source: sqlite3.Row, target: sqlite3.Row, heuristics: tuple[str, ...]
    ) -> str:
        documents = sorted(
            (
                {"stable_id": row["identity_key"], "source_content_hash": row["source_content_hash"]}
                for row in (source, target)
            ),
            key=lambda item: item["stable_id"],
        )
        material = {
            "documents": documents,
            "heuristics": sorted(heuristics),
            "prompt_version": self.prompt_version,
            "schema_version": RELATION_SCHEMA_VERSION,
        }
        return hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _complete_with_retry(self, source: sqlite3.Row, target: sqlite3.Row):
        user = json.dumps(
            {
                "source": {
                    "document_id": source["identity_key"],
                    "title": source["title"] or "",
                    "content": source["plain_content"] or "",
                },
                "target": {
                    "document_id": target["identity_key"],
                    "title": target["title"] or "",
                    "content": target["plain_content"] or "",
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for attempt in range(len(self.retry_delays) + 1):
            try:
                return self.provider.complete(system=RELATION_SYSTEM_PROMPT, user=user)
            except (ProviderRateLimitError, ProviderTemporaryError):
                if attempt == len(self.retry_delays):
                    raise
                self.sleeper(self.retry_delays[attempt])
        raise AssertionError("unreachable")

    @staticmethod
    def _payload(relation: ValidatedRelation, pair: CandidatePair) -> dict[str, object]:
        evidence = [
            {"document_id": document_id, "excerpt": excerpt}
            for document_id, excerpt in relation.evidence
        ]
        return {
            "type": relation.relation_type,
            "score": relation.score,
            "evidence": evidence,
            "explanation": relation.explanation,
            "candidate_evidence": {
                "candidate_score": pair.score,
                "heuristics": list(sorted(pair.evidence_sources)),
            },
        }

    def _store(
        self,
        connection: sqlite3.Connection,
        source: sqlite3.Row,
        target: sqlite3.Row,
        pair: CandidatePair,
        input_hash: str,
        derivation_id: str,
        payload: dict[str, object],
    ) -> None:
        source_hashes = sorted((source["source_content_hash"], target["source_content_hash"]))
        normalized_hashes = sorted(
            (source["normalized_content_hash"], target["normalized_content_hash"])
        )
        with connection:
            connection.execute(
                """INSERT INTO derivations
                   (id, document_id, kind, payload_json, input_hash, source_content_hash,
                    normalized_content_hash, normalization_version, schema_version,
                    prompt_version, provider, model, generation_parameters_json, status)
                   VALUES (?, ?, 'relation', ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', 'accepted')""",
                (
                    derivation_id,
                    pair.source_document_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    input_hash,
                    hashlib.sha256("\0".join(source_hashes).encode()).hexdigest(),
                    hashlib.sha256("\0".join(normalized_hashes).encode()).hexdigest(),
                    max(source["normalization_version"], target["normalization_version"]),
                    RELATION_SCHEMA_VERSION,
                    self.prompt_version,
                    self.provider.provider_name,
                    self.provider.model_name,
                ),
            )
            self._reconcile(connection, pair, derivation_id, payload, promote=True)

    @staticmethod
    def _reconcile(
        connection: sqlite3.Connection,
        pair: CandidatePair,
        derivation_id: str,
        payload: Mapping[str, object],
        *,
        promote: bool,
    ) -> None:
        state = connection.execute(
            """SELECT derivation_id FROM relation_pair_state
               WHERE source_document_id=? AND target_document_id=?""",
            (pair.source_document_id, pair.target_document_id),
        ).fetchone()
        if not promote and state is not None and state[0] != derivation_id:
            return
        if promote or state is None:
            connection.execute(
                """INSERT INTO relation_pair_state
                   (source_document_id, target_document_id, derivation_id, decision_type)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(source_document_id, target_document_id) DO UPDATE SET
                     derivation_id=excluded.derivation_id,
                     decision_type=excluded.decision_type,
                     updated_at=CURRENT_TIMESTAMP""",
                (
                    pair.source_document_id,
                    pair.target_document_id,
                    derivation_id,
                    payload["type"],
                ),
            )
        connection.execute(
            """DELETE FROM relations
               WHERE (source_document_id=? AND target_document_id=?)
                  OR (source_document_id=? AND target_document_id=?)""",
            (
                pair.source_document_id,
                pair.target_document_id,
                pair.target_document_id,
                pair.source_document_id,
            ),
        )
        if payload["type"] == "no_relation":
            return
        connection.execute(
            """INSERT INTO relations
               (source_document_id, target_document_id, relation_type, score, evidence,
                derivation_id, candidate_evidence_json, explanation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pair.source_document_id,
                pair.target_document_id,
                payload["type"],
                payload["score"],
                json.dumps(payload["evidence"], ensure_ascii=False, sort_keys=True),
                derivation_id,
                json.dumps(payload["candidate_evidence"], ensure_ascii=False, sort_keys=True),
                payload["explanation"],
            ),
        )

    def _append_log(
        self,
        pair: CandidatePair,
        derivation_id: str | None,
        status: str,
        error_code: str | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_document_id": pair.source_document_id,
            "target_document_id": pair.target_document_id,
            "prompt_version": self.prompt_version,
            "provider": self.provider.provider_name,
            "model": self.provider.model_name,
            "derivation_id": derivation_id,
            "status": status,
            "error_code": error_code,
        }
        self.run_log.parent.mkdir(parents=True, exist_ok=True)
        with self.run_log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class RelationCandidateBuilder:
    """Union independently bounded heuristics into stable candidate lists."""

    def __init__(
        self,
        database: str | Path,
        *,
        heuristic_limit: int = 20,
        read_only: bool = False,
    ) -> None:
        _bounded("heuristic_limit", heuristic_limit, MAX_HEURISTIC_LIMIT)
        if read_only:
            uri = Path(database).resolve().as_uri() + "?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
        else:
            self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.create_function(
            "derivation_input_hash", 3, derivation_input_hash, deterministic=True
        )
        if not read_only:
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
