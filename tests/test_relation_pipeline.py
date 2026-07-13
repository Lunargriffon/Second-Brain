from __future__ import annotations

import json
import math
import sqlite3

import pytest

from pkb.cli import main
from pkb.derive.provider import (
    FakeDerivationProvider,
    ProviderAuthError,
    ProviderTemporaryError,
)
from pkb.derive.relations import RelationPipeline, validate_relation
from pkb.knowledge.migrations import migrate


def _database(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    database = tmp_path / "relations.sqlite3"
    connection = sqlite3.connect(database)
    migrate(connection)
    for document_id, identity, title, content in (
        (1, "doc:alpha", "Alpha", "Alpha source contains grounded evidence."),
        (2, "doc:beta", "Beta", "Beta source discusses the same architecture."),
    ):
        connection.execute(
            """INSERT INTO documents
               (id, identity_key, title, plain_content, source_content_hash,
                normalized_content_hash, normalization_version, schema_version)
               VALUES (?, ?, ?, ?, ?, ?, 1, 1)""",
            (document_id, identity, title, content, f"source-{document_id}", f"norm-{document_id}"),
        )
        connection.execute(
            """INSERT INTO source_memberships
               (document_id, source, source_item_id, collection_id, observed_at)
               VALUES (?, 'test', ?, 'saved', ?)""",
            (document_id, str(document_id), f"2026-01-0{document_id}T00:00:00Z"),
        )
    connection.commit()
    connection.close()
    return database


def _response(**changes):
    payload = {
        "type": "supports",
        "score": 0.9,
        "evidence": [{"document_id": "doc:alpha", "excerpt": "grounded   evidence"}],
        "explanation": "doc:alpha supports doc:beta through the shared architecture claim.",
    }
    payload.update(changes)
    return payload


def test_relation_without_source_evidence_is_rejected(tmp_path):
    database = _database(tmp_path)
    provider = FakeDerivationProvider([_response(evidence=[
        {"document_id": "doc:alpha", "excerpt": "not in either source"}
    ])])
    result = RelationPipeline(database, provider, run_log=tmp_path / "runs.jsonl").run(
        document_limit=2, per_document_limit=1, pair_limit=1
    )
    assert result.accepted == 0
    assert result.invalid == 1


@pytest.mark.parametrize("relation_type", [
    "supports", "contrasts", "extends", "example_of", "similar_to",
])
def test_allowed_relation_types_are_stored_with_grounding_and_candidate_evidence(
    tmp_path, relation_type
):
    database = _database(tmp_path)
    provider = FakeDerivationProvider([_response(type=relation_type)])
    result = RelationPipeline(database, provider, run_log=tmp_path / "runs.jsonl").run(
        document_limit=2, per_document_limit=1, pair_limit=1
    )
    assert result.accepted == 1
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM relations").fetchone()
        derivation = connection.execute(
            "SELECT kind, payload_json, prompt_version, status FROM derivations WHERE id=?",
            (row["derivation_id"],),
        ).fetchone()
    assert (row["source_document_id"], row["target_document_id"]) == (1, 2)
    assert row["relation_type"] == relation_type
    assert json.loads(row["evidence"]) == [
        {"document_id": "doc:alpha", "excerpt": "grounded evidence"}
    ]
    assert "doc:alpha" in row["explanation"] and "doc:beta" in row["explanation"]
    assert json.loads(row["candidate_evidence_json"]) == {
        "candidate_score": pytest.approx(0.77419355),
        "heuristics": ["same_collection_time"],
    }
    assert derivation[0] == "relation" and derivation[2] == "relation-v1"
    assert derivation[3] == "accepted"
    assert json.loads(derivation[1])["candidate_evidence"]["heuristics"] == ["same_collection_time"]


@pytest.mark.parametrize(
    "payload",
    [
        _response(type="related"),
        _response(score=-0.1),
        _response(score=1.1),
        _response(score=math.inf),
        _response(explanation="documents 1 and 2 are related"),
        _response(evidence=[{"document_id": "doc:beta", "excerpt": "grounded evidence"}]),
        _response(extra="unknown"),
        {"type": "supports", "score": 0.9, "evidence": []},
    ],
)
def test_relation_schema_is_strict_and_references_both_stable_ids(payload):
    with pytest.raises(ValueError):
        validate_relation(
            payload,
            source_text="Alpha source contains grounded evidence.",
            target_text="Beta source.",
            source_stable_id="doc:alpha",
            target_stable_id="doc:beta",
        )


def test_canonical_pair_is_not_called_twice_and_new_prompt_keeps_old_derivation(tmp_path):
    database = _database(tmp_path)
    first = FakeDerivationProvider([_response()])
    RelationPipeline(database, first, run_log=tmp_path / "first.jsonl").run(
        document_limit=2, per_document_limit=1, pair_limit=1
    )
    second = FakeDerivationProvider([
        _response(type="contrasts", score=0.8, explanation="doc:alpha contrasts doc:beta.")
    ])
    result = RelationPipeline(
        database, second, run_log=tmp_path / "second.jsonl", prompt_version="relation-v2"
    ).run(document_limit=2, per_document_limit=1, pair_limit=1)
    with sqlite3.connect(database) as connection:
        relation_count = connection.execute("SELECT count(*) FROM relations").fetchone()[0]
        rows = connection.execute(
            "SELECT prompt_version, status FROM derivations WHERE kind='relation' ORDER BY prompt_version"
        ).fetchall()
    assert result.accepted == 1
    assert relation_count == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT relation_type FROM relations").fetchone()[0] == "contrasts"
    assert rows == [("relation-v1", "accepted"), ("relation-v2", "accepted")]
    assert len(first.requests) == 1 and len(second.requests) == 1


def test_same_input_is_idempotent_and_no_relation_removes_only_current_projection(tmp_path):
    database = _database(tmp_path)
    first = FakeDerivationProvider([_response()])
    pipeline = RelationPipeline(database, first, run_log=tmp_path / "first.jsonl")
    pipeline.run(document_limit=2, per_document_limit=1, pair_limit=1)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM relations")
        connection.commit()
    pipeline.run(document_limit=2, per_document_limit=1, pair_limit=1)
    with sqlite3.connect(database) as connection:
        restored = connection.execute("SELECT count(*) FROM relations").fetchone()[0]
    assert len(first.requests) == 1
    assert restored == 1

    no_relation = _response(type="no_relation", score=0, evidence=[])
    second = FakeDerivationProvider([no_relation])
    result = RelationPipeline(
        database, second, run_log=tmp_path / "second.jsonl", prompt_version="relation-v2"
    ).run(document_limit=2, per_document_limit=1, pair_limit=1)
    pipeline.run(document_limit=2, per_document_limit=1, pair_limit=1)
    with sqlite3.connect(database) as connection:
        current = connection.execute("SELECT count(*) FROM relations").fetchone()[0]
        audit = connection.execute(
            "SELECT count(*) FROM derivations WHERE kind='relation' AND status='accepted'"
        ).fetchone()[0]
    assert result.no_relation == 1 and current == 0 and audit == 2


def test_temporary_errors_retry_bounded_and_permanent_error_stops_safely(tmp_path):
    database = _database(tmp_path)
    provider = FakeDerivationProvider([
        ProviderTemporaryError("private body"),
        _response(),
    ])
    result = RelationPipeline(
        database, provider, run_log=tmp_path / "retry.jsonl", retry_delays=(0,), sleeper=lambda _: None
    ).run(document_limit=2, per_document_limit=1, pair_limit=1)
    assert result.accepted == 1 and len(provider.requests) == 2

    database = _database(tmp_path / "other")
    provider = FakeDerivationProvider([ProviderAuthError("secret token")])
    log = tmp_path / "permanent.jsonl"
    result = RelationPipeline(database, provider, run_log=log).run(
        document_limit=2, per_document_limit=1, pair_limit=1
    )
    assert result.stopped and result.failed == 1
    assert "secret" not in log.read_text(encoding="utf-8")


def test_relations_cli_dry_run_is_read_only_and_never_builds_provider(tmp_path, capsys):
    database = _database(tmp_path)
    before = database.read_bytes()
    provider = FakeDerivationProvider([_response()])
    report = tmp_path / "must-not-exist.jsonl"
    code = main([
        "derive", "relations", "--db", str(database), "--document-limit", "2",
        "--per-document-limit", "1", "--pair-limit", "1", "--dry-run",
        "--report", str(report),
    ], provider=provider)
    output = capsys.readouterr()
    assert code == 0
    assert "documents=2" in output.out and "pairs=1" in output.out
    assert "doc:alpha" not in output.out + output.err
    assert provider.requests == [] and database.read_bytes() == before and not report.exists()


def test_relations_cli_dry_run_does_not_migrate_an_older_database(tmp_path, capsys):
    from pkb.knowledge.migrations import _MIGRATIONS

    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        for version in range(1, 6):
            for statement in _MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={version}")
        for document_id, identity in ((1, "doc:alpha"), (2, "doc:beta")):
            connection.execute(
                """INSERT INTO documents
                   (id, identity_key, title, plain_content, source_content_hash,
                    normalized_content_hash, normalization_version, schema_version)
                   VALUES (?, ?, 'title', 'content', ?, ?, 1, 1)""",
                (document_id, identity, f"s{document_id}", f"n{document_id}"),
            )
            connection.execute(
                """INSERT INTO source_memberships
                   (document_id, source, source_item_id, collection_id, observed_at)
                   VALUES (?, 'test', ?, 'saved', ?)""",
                (document_id, str(document_id), f"2026-01-0{document_id}T00:00:00Z"),
            )
    before = database.read_bytes()
    code = main([
        "derive", "relations", "--db", str(database), "--document-limit", "2",
        "--per-document-limit", "1", "--pair-limit", "1", "--dry-run",
    ], provider=FakeDerivationProvider([]))
    assert code == 0
    assert database.read_bytes() == before
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    assert "pairs=1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--document-limit", "0", "--per-document-limit", "1", "--pair-limit", "1"],
        ["--document-limit", "2", "--per-document-limit", "201", "--pair-limit", "1"],
        ["--document-limit", "2", "--per-document-limit", "1", "--pair-limit", "100001"],
    ],
)
def test_relations_cli_rejects_missing_or_invalid_bounds(tmp_path, capsys, args):
    database = _database(tmp_path)
    code = main(["derive", "relations", "--db", str(database), *args], provider=FakeDerivationProvider([]))
    assert code == 2
    assert capsys.readouterr().err
