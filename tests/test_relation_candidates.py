from __future__ import annotations

import json
import sqlite3

import pytest

from pkb.derive.relations import Candidate, RelationCandidateBuilder
from pkb.knowledge.fingerprint import derivation_input_hash
from pkb.knowledge.migrations import migrate
from pkb.knowledge.search import SearchIndex


def _document(connection: sqlite3.Connection, document_id: int, title: str) -> None:
    connection.execute(
        """INSERT INTO documents
           (id, identity_key, title, plain_content, source_content_hash,
            normalized_content_hash, normalization_version, schema_version,
            first_saved_at, source_observed_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?)""",
        (
            document_id,
            f"doc:{document_id}",
            title,
            f"{title} content",
            f"source-{document_id}",
            f"normalized-{document_id}",
            f"2026-01-{document_id:02d}T00:00:00+00:00",
            f"2026-01-{document_id:02d}T00:00:00+00:00",
        ),
    )


def _label(
    connection: sqlite3.Connection,
    document_id: int,
    name: str,
    *,
    kind: str = "tag",
    confidence: float = 0.9,
    origin: str = "user",
) -> None:
    table = "tags" if kind == "tag" else "topics"
    join_table = "document_tags" if kind == "tag" else "document_topics"
    id_column = "tag_id" if kind == "tag" else "topic_id"
    connection.execute(
        f"INSERT OR IGNORE INTO {table}(normalized_name, display_name) VALUES (?, ?)",
        (name.casefold(), name),
    )
    label_id = connection.execute(
        f"SELECT id FROM {table} WHERE normalized_name=?", (name.casefold(),)
    ).fetchone()[0]
    derivation = connection.execute(
        "SELECT id FROM derivations WHERE document_id=? ORDER BY id DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    connection.execute(
        f"""INSERT INTO {join_table}
            (document_id, {id_column}, origin, confidence)
            VALUES (?, ?, ?, ?)""",
        (document_id, label_id, origin, confidence),
    )
    if origin == "ai":
        connection.execute(
            f"UPDATE {join_table} SET derivation_id=? WHERE document_id=? AND {id_column}=? AND origin='ai'",
            (derivation[0] if derivation else None, document_id, label_id),
        )


def _membership(
    connection: sqlite3.Connection,
    document_id: int,
    collection_id: str,
    observed_at: str,
) -> None:
    connection.execute(
        """INSERT INTO source_memberships
           (document_id, source, source_item_id, collection_id, observed_at)
           VALUES (?, 'test', ?, ?, ?)""",
        (document_id, str(document_id), collection_id, observed_at),
    )


def _derivation(connection: sqlite3.Connection, document_id: int, key_point: str) -> None:
    row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    connection.execute(
        """INSERT INTO derivations
           (id, document_id, kind, payload_json, input_hash, source_content_hash,
            normalized_content_hash, normalization_version, schema_version,
            prompt_version, status)
           VALUES (?, ?, 'article', ?, ?, ?, ?, 1, 1, 'article-v1', 'accepted')""",
        (
            f"derivation-{document_id}",
            document_id,
            json.dumps({"summary": key_point, "key_points": [key_point]}),
            derivation_input_hash(
                row["source_content_hash"],
                row["normalized_content_hash"],
                row["normalization_version"],
            ),
            row["source_content_hash"],
            row["normalized_content_hash"],
        ),
    )


@pytest.fixture
def relation_db(tmp_path):
    database = tmp_path / "knowledge.sqlite3"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    for document_id, title in (
        (1, "SQLite knowledge architecture"),
        (2, "SQLite search design"),
        (3, "Personal knowledge workflow"),
        (4, "Unrelated gardening notes"),
        (5, "SQLite architecture patterns"),
    ):
        _document(connection, document_id, title)
    for document_id in (1, 2, 3):
        _label(connection, document_id, "knowledge", kind="tag")
    for document_id in (1, 3, 5):
        _label(connection, document_id, "architecture", kind="topic")
    _derivation(connection, 4, "gardening")
    _label(connection, 4, "knowledge", confidence=0.2, origin="ai")
    _membership(connection, 1, "saved", "2026-01-01T00:00:00+00:00")
    _membership(connection, 4, "saved", "2026-01-02T00:00:00+00:00")
    _membership(connection, 5, "saved", "2026-02-20T00:00:00+00:00")
    _derivation(connection, 1, "SQLite search")
    connection.commit()
    search = SearchIndex(database)
    search.rebuild()
    search.update_derived_projection(1, summary="knowledge architecture", tags=("knowledge",))
    search.update_derived_projection(2, summary="SQLite knowledge search", tags=("knowledge",))
    search.update_derived_projection(5, summary="architecture patterns", tags=())
    search.close()
    connection.close()
    return database


def test_relation_candidates_are_deduplicated_self_excluded_and_capped(relation_db):
    with RelationCandidateBuilder(relation_db, heuristic_limit=10) as builder:
        candidates = builder.for_document(1, per_document_limit=3)

    assert len(candidates) == 3
    assert len({item.document_id for item in candidates}) == len(candidates)
    assert all(isinstance(item, Candidate) for item in candidates)
    assert all(item.document_id != 1 for item in candidates)
    assert all(item.evidence_sources for item in candidates)
    assert {"shared_tag", "shared_topic", "fts_title", "same_collection_time"} <= {
        source for item in candidates for source in item.evidence_sources
    }


def test_candidate_scores_and_ties_have_stable_order(relation_db):
    with RelationCandidateBuilder(relation_db) as builder:
        first = builder.for_document(1, per_document_limit=5)
        second = builder.for_document(1, per_document_limit=5)

    assert first == second
    assert first == tuple(sorted(first, key=lambda item: (-item.score, item.document_id)))
    with pytest.raises(Exception):
        first[0].score = 0  # type: ignore[misc]


def test_each_candidate_query_is_limited_and_never_builds_all_pairs(relation_db):
    statements: list[str] = []
    with RelationCandidateBuilder(relation_db, heuristic_limit=4) as builder:
        builder.connection.set_trace_callback(statements.append)
        builder.for_document(1, per_document_limit=5)

    selects = [statement.upper() for statement in statements if statement.lstrip().upper().startswith(("SELECT", "WITH"))]
    candidate_queries = [
        statement
        for statement in selects
        if "SELECT CANDIDATE.DOCUMENT_ID" in statement
        or "JOIN DOCUMENTS_SEARCH_CONTENT AS CANDIDATE" in statement
    ]
    assert candidate_queries
    assert all("LIMIT" in statement for statement in candidate_queries)
    assert all("CROSS JOIN" not in statement for statement in candidate_queries)


def test_low_confidence_labels_do_not_admit_candidates(relation_db):
    with RelationCandidateBuilder(relation_db) as builder:
        candidates = builder.for_document(4, per_document_limit=5)

    evidence = {item.document_id: item.evidence_sources for item in candidates}
    assert "shared_tag" not in evidence.get(1, ())


def test_stale_ai_labels_are_ignored_but_user_labels_remain_valid(relation_db):
    connection = sqlite3.connect(relation_db)
    _label(connection, 4, "architecture", kind="topic", confidence=0.95, origin="ai")
    connection.execute(
        "UPDATE documents SET normalized_content_hash='edited' WHERE id=4"
    )
    connection.commit()
    connection.close()

    with RelationCandidateBuilder(relation_db) as builder:
        candidates = builder.for_document(4, per_document_limit=5)

    evidence = {item.document_id: item.evidence_sources for item in candidates}
    assert "shared_topic" not in evidence.get(1, ())
    # Document 4's stale AI tag is ignored; document 1's user-owned labels remain
    # available to other documents and never require a derivation row.
    assert "shared_tag" not in evidence.get(1, ())


def test_stale_derivation_summary_and_key_points_are_not_fts_seeds(relation_db):
    connection = sqlite3.connect(relation_db)
    connection.execute(
        "UPDATE documents SET normalized_content_hash='edited' WHERE id=1"
    )
    connection.commit()
    connection.close()

    with RelationCandidateBuilder(relation_db) as builder:
        candidates = builder.for_document(1, per_document_limit=5)

    derived_evidence = {
        source
        for candidate in candidates
        for source in candidate.evidence_sources
        if source in {"fts_summary", "fts_key_point"}
    }
    assert derived_evidence == set()


def test_time_neighbors_use_indexed_before_and_after_range_queries(relation_db):
    statements: list[str] = []
    with RelationCandidateBuilder(relation_db, heuristic_limit=4) as builder:
        plan = builder.connection.execute(
            """EXPLAIN QUERY PLAN SELECT document_id FROM source_memberships
               WHERE source=? AND collection_id=? AND observed_at<?
               ORDER BY observed_at DESC LIMIT ?""",
            ("test", "saved", "2026-02-01", 4),
        ).fetchall()
        builder.connection.set_trace_callback(statements.append)
        builder.for_document(1, per_document_limit=5)

    assert any("idx_memberships_collection_time" in row[3] for row in plan)
    ranges = [
        statement.upper()
        for statement in statements
        if "FROM SOURCE_MEMBERSHIPS AS CANDIDATE" in statement.upper()
    ]
    assert len(ranges) >= 2
    assert all("LIMIT" in statement for statement in ranges)
    assert any("OBSERVED_AT<" in statement for statement in ranges)
    assert any("OBSERVED_AT>=" in statement for statement in ranges)


def test_corpus_dry_run_stops_at_exact_pair_limit_without_provider(relation_db):
    with RelationCandidateBuilder(relation_db) as builder:
        result = builder.dry_run(
            document_limit=5,
            per_document_limit=5,
            pair_limit=4,
        )

    assert result.pair_count == 4
    assert len(result.pairs) == 4
    assert len({(pair.source_document_id, pair.target_document_id) for pair in result.pairs}) == 4
    assert all(pair.source_document_id < pair.target_document_id for pair in result.pairs)


@pytest.mark.parametrize(
    ("method", "kwargs"),
    (
        ("for_document", {"document_id": 1, "per_document_limit": 0}),
        ("for_document", {"document_id": 1, "per_document_limit": 201}),
        ("dry_run", {"document_limit": 0, "per_document_limit": 5, "pair_limit": 5}),
        ("dry_run", {"document_limit": 5, "per_document_limit": 0, "pair_limit": 5}),
        ("dry_run", {"document_limit": 5, "per_document_limit": 5, "pair_limit": 0}),
        ("dry_run", {"document_limit": 10_001, "per_document_limit": 5, "pair_limit": 5}),
        ("dry_run", {"document_limit": 5, "per_document_limit": 5, "pair_limit": 100_001}),
    ),
)
def test_bounds_must_be_positive_and_reasonable(relation_db, method, kwargs):
    with RelationCandidateBuilder(relation_db) as builder:
        with pytest.raises(ValueError):
            getattr(builder, method)(**kwargs)


def test_missing_fts_and_labels_degrade_to_time_candidates(relation_db):
    connection = sqlite3.connect(relation_db)
    connection.execute("DROP TABLE documents_fts")
    connection.execute("DELETE FROM document_tags")
    connection.execute("DELETE FROM document_topics")
    connection.commit()
    connection.close()

    with RelationCandidateBuilder(relation_db) as builder:
        candidates = builder.for_document(1, per_document_limit=5)

    assert candidates
    assert all(item.evidence_sources == ("same_collection_time",) for item in candidates)
