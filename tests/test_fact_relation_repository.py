from __future__ import annotations

from types import SimpleNamespace

import pytest

from pkb.knowledge.entity_repository import EntityRepository
from pkb.knowledge.fact_models import ScalarObject
from pkb.knowledge import fact_repository as fact_repository_module
from pkb.knowledge.fact_repository import FactRepository


@pytest.fixture
def fact_repository(tmp_path):
    repository = FactRepository(tmp_path / "knowledge.db")
    yield repository
    repository.close()


def _accepted_entity(repository: FactRepository, name: str) -> int:
    entities = EntityRepository(repository.database)
    try:
        entity_id = entities.create_candidate("person", name, actor="test")
        entities.accept(entity_id, actor="human:test", reason="confirmed")
        return entity_id
    finally:
        entities.close()


def _text_version(repository: FactRepository, ordinal: int, content: str) -> int:
    cursor = repository.connection.execute(
        """INSERT INTO documents
           (identity_key, source_type, title, plain_content, source_content_hash,
            normalized_content_hash, normalization_version, schema_version)
           VALUES (?, 'local', ?, ?, ?, ?, 1, 1)""",
        (f"relation:{ordinal}", f"relation {ordinal}", content,
         f"source:{ordinal}", f"normalized:{ordinal}"),
    )
    document_id = int(cursor.lastrowid)
    cursor = repository.connection.execute(
        """INSERT INTO document_text_versions
           (document_id, source_content_hash, normalized_content_hash,
            normalization_version, plain_content)
           VALUES (?, ?, ?, 1, ?)""",
        (document_id, f"source:{ordinal}", f"normalized:{ordinal}", content),
    )
    repository.connection.commit()
    return int(cursor.lastrowid)


def _accepted_fact(repository: FactRepository, subject_id: int, ordinal: int):
    content = f"claim {ordinal}"
    version_id = _text_version(repository, ordinal, content)
    fact = repository.create_candidate(
        subject_id, "status", ScalarObject("string", content), actor="test"
    )
    evidence = repository.add_evidence(
        fact.id, version_id, role="supports", start=0, end=len(content), excerpt=content
    )
    accepted = repository.accept(fact.id, actor="human:test", reason="confirmed")
    return SimpleNamespace(**accepted.__dict__, evidence_ids=(evidence.id,))


@pytest.fixture
def three_accepted_facts(fact_repository):
    subject = _accepted_entity(fact_repository, "Subject")
    return tuple(_accepted_fact(fact_repository, subject, ordinal) for ordinal in range(1, 4))


def _candidate(repository, left, right, relation_type):
    return repository.create_relation_candidate(
        left.id,
        right.id,
        relation_type,
        explanation=f"{relation_type} evidence",
        evidence_ids=[left.evidence_ids[0], right.evidence_ids[0]],
        actor="deterministic:relation-v1",
    )


def test_valid_pending_conflict_marks_both_facts_disputed(
    fact_repository, three_accepted_facts
):
    first, second, _ = three_accepted_facts
    relation = _candidate(fact_repository, second, first, "conflicts_with")

    assert relation.left_fact_id < relation.right_fact_id
    assert relation.review_status == "pending"
    assert relation.deterministic_validation_status == "passed"
    assert fact_repository.get(first.id).knowledge_status == "disputed"
    assert fact_repository.get(second.id).knowledge_status == "disputed"


def test_refines_preserves_direction(fact_repository, three_accepted_facts):
    newer, older, _ = three_accepted_facts
    relation = _candidate(fact_repository, newer, older, "refines")
    assert (relation.left_fact_id, relation.right_fact_id) == (newer.id, older.id)


def test_supersedes_cycle_is_rejected(fact_repository, three_accepted_facts):
    a, b, c = three_accepted_facts
    first = _candidate(fact_repository, a, b, "supersedes")
    fact_repository.accept_relation(first.id, actor="human:test", reason="newer")
    second = _candidate(fact_repository, b, c, "supersedes")
    fact_repository.accept_relation(second.id, actor="human:test", reason="newer")

    third = _candidate(fact_repository, c, a, "supersedes")
    with pytest.raises(fact_repository_module.FactRelationCycleError):
        fact_repository.accept_relation(third.id, actor="human:test", reason="cycle")


def test_relation_evidence_must_belong_to_an_endpoint(fact_repository, three_accepted_facts):
    first, second, outsider = three_accepted_facts
    with pytest.raises(fact_repository_module.FactRelationDecisionError, match="endpoint"):
        fact_repository.create_relation_candidate(
            first.id, second.id, "refines", explanation="invalid evidence",
            evidence_ids=[outsider.evidence_ids[0]], actor="deterministic:test",
        )


def test_rejecting_conflict_requires_explicit_human_resolution(
    fact_repository, three_accepted_facts
):
    first, second, _ = three_accepted_facts
    relation = _candidate(fact_repository, first, second, "conflicts_with")
    fact_repository.reject_relation(relation.id, actor="human:test", reason="false positive")
    assert fact_repository.get(first.id).knowledge_status == "disputed"

    fact_repository.resolve_dispute(
        [first.id, second.id], actor="human:test", reason="conflict rejected"
    )
    assert fact_repository.get(first.id).knowledge_status == "active"
    assert fact_repository.get(second.id).knowledge_status == "active"


def test_relation_decisions_require_human_actor(fact_repository, three_accepted_facts):
    first, second, _ = three_accepted_facts
    relation = _candidate(fact_repository, first, second, "refines")
    with pytest.raises(fact_repository_module.FactRelationDecisionError, match="human"):
        fact_repository.accept_relation(
            relation.id, actor="deterministic:test", reason="not authorized"
        )


def test_supersede_and_retract_preserve_rows(fact_repository, three_accepted_facts):
    newer, older, withdrawn = three_accepted_facts
    relation = fact_repository.supersede(
        newer.id, older.id,
        explanation="new observation", evidence_ids=[newer.evidence_ids[0], older.evidence_ids[0]],
        actor="human:test", reason="time ordered",
    )
    assert relation.review_status == "accepted"
    assert fact_repository.get(older.id).knowledge_status == "superseded"

    fact_repository.retract(withdrawn.id, actor="human:test", reason="source withdrew claim")
    assert fact_repository.get(withdrawn.id).knowledge_status == "retracted"
    assert fact_repository.connection.execute(
        "SELECT count(*) FROM facts WHERE id=?", (withdrawn.id,)
    ).fetchone()[0] == 1
