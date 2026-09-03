from __future__ import annotations

from dataclasses import dataclass
import sqlite3

import pytest

from pkb.knowledge.entity_repository import EntityRepository
from pkb.knowledge.fact_models import EntityObject
from pkb.knowledge.fact_repository import FactRepository


@dataclass(frozen=True)
class KnowledgeFixture:
    repository: FactRepository
    entities: EntityRepository
    person_id: int
    organization_id: int


@pytest.fixture
def knowledge(tmp_path):
    database = tmp_path / "knowledge.db"
    entities = EntityRepository(database)
    repository = FactRepository(database)
    person_id = entities.create_candidate("person", "Zhang San", actor="test")
    organization_id = entities.create_candidate("organization", "OpenAI", actor="test")
    entities.accept(person_id, actor="human:test", reason="confirmed")
    entities.accept(organization_id, actor="human:test", reason="confirmed")
    yield KnowledgeFixture(repository, entities, person_id, organization_id)
    repository.close()
    entities.close()


def _text_version(repository: FactRepository, identity: str, content: str) -> int:
    cursor = repository.connection.execute(
        """INSERT INTO documents
           (identity_key, source_type, title, plain_content, source_content_hash,
            normalized_content_hash, normalization_version, schema_version)
           VALUES (?, 'local', ?, ?, ?, ?, 1, 1)""",
        (identity, identity, content, f"source:{identity}", f"normalized:{identity}"),
    )
    document_id = int(cursor.lastrowid)
    cursor = repository.connection.execute(
        """INSERT INTO document_text_versions
           (document_id, source_content_hash, normalized_content_hash,
            normalization_version, plain_content)
           VALUES (?, ?, ?, 1, ?)""",
        (document_id, f"source:{identity}", f"normalized:{identity}", content),
    )
    repository.connection.commit()
    return int(cursor.lastrowid)


def _accepted_fact(knowledge: KnowledgeFixture, identity: str = "one"):
    version_id = _text_version(knowledge.repository, identity, "Zhang works at OpenAI")
    fact = knowledge.repository.create_candidate(
        knowledge.person_id,
        "works_at",
        EntityObject(knowledge.organization_id),
        actor="test",
        valid_from={"one": "2026-01-01", "first": "2026-02-01", "second": "2026-03-01"}[identity],
    )
    evidence = knowledge.repository.add_evidence(
        fact.id, version_id, role="supports", start=0, end=5, excerpt="Zhang"
    )
    fact = knowledge.repository.accept(fact.id, actor="human:test", reason="confirmed")
    return fact, evidence, version_id


def test_invalidating_last_evidence_reopens_fact(knowledge):
    fact, evidence, _ = _accepted_fact(knowledge)

    affected = knowledge.repository.invalidate_evidence(
        evidence.id, actor="system:normalization-v2", reason="text version stale"
    )

    fact = knowledge.repository.get(fact.id)
    assert affected == (fact.id,)
    assert (fact.review_status, fact.knowledge_status) == ("pending", None)


def test_invalidating_one_of_two_sources_keeps_fact_active(knowledge):
    fact, evidence, _ = _accepted_fact(knowledge)
    second_version = _text_version(knowledge.repository, "two", "Zhang remains at OpenAI")
    knowledge.repository.add_evidence(
        fact.id, second_version, role="supports", start=0, end=5, excerpt="Zhang"
    )

    affected = knowledge.repository.invalidate_evidence(
        evidence.id, actor="system:source-update", reason="source changed"
    )

    fact = knowledge.repository.get(fact.id)
    assert affected == ()
    assert (fact.review_status, fact.knowledge_status) == ("accepted", "active")


def test_repeating_invalidation_is_idempotent(knowledge):
    fact, evidence, _ = _accepted_fact(knowledge)

    first = knowledge.repository.invalidate_evidence(
        evidence.id, actor="system:test", reason="stale"
    )
    second = knowledge.repository.invalidate_evidence(
        evidence.id, actor="system:test", reason="stale"
    )

    assert first == (fact.id,)
    assert second == ()


def test_text_version_invalidation_is_bounded_and_reports_mentions(knowledge):
    fact, _, version_id = _accepted_fact(knowledge)
    mention = knowledge.entities.add_mention(
        version_id, "Zhang", 0, 5, "person", entity_id=knowledge.person_id,
        method="strong_identifier", accept=True,
    )

    first = knowledge.repository.invalidate_text_version(
        version_id, limit=1, actor="system:test", reason="normalization changed"
    )
    second = knowledge.repository.invalidate_text_version(
        version_id, limit=1, actor="system:test", reason="normalization changed"
    )

    assert sum(map(len, (first.mention_ids, first.evidence_ids))) == 1
    assert set(first.mention_ids + second.mention_ids) == {mention.id}
    assert set(first.evidence_ids + second.evidence_ids)
    assert first.fact_ids + second.fact_ids == (fact.id,)


def test_invalid_evidence_reopens_accepted_relation(knowledge):
    first, first_evidence, _ = _accepted_fact(knowledge, "first")
    second, second_evidence, _ = _accepted_fact(knowledge, "second")
    relation = knowledge.repository.create_relation_candidate(
        first.id, second.id, "refines", explanation="more precise",
        evidence_ids=[first_evidence.id, second_evidence.id], actor="deterministic:test",
    )
    knowledge.repository.accept_relation(
        relation.id, actor="human:test", reason="confirmed"
    )

    knowledge.repository.invalidate_evidence(
        first_evidence.id, actor="system:test", reason="stale"
    )
    result = knowledge.repository.invalidate_evidence(
        second_evidence.id, actor="system:test", reason="stale"
    )

    relation = knowledge.repository.get_relation(relation.id)
    assert relation.review_status == "pending"
    assert relation.knowledge_status is None
    assert set(result) == {second.id}


def test_reopening_entity_reopens_dependent_facts_and_clears_merge(knowledge):
    fact, _, _ = _accepted_fact(knowledge)
    winner = knowledge.entities.create_candidate("person", "Canonical Zhang", actor="test")
    knowledge.entities.accept(winner, actor="human:test", reason="confirmed")
    knowledge.entities.merge(
        knowledge.person_id, winner, actor="human:test", reason="same person"
    )

    reopened = knowledge.entities.reopen(
        knowledge.person_id, actor="system:evidence", reason="identity evidence stale"
    )

    assert reopened.review_status == "pending"
    assert reopened.merged_into_entity_id is None
    assert knowledge.repository.get(fact.id).review_status == "pending"


def test_direct_entity_reopen_is_rejected_while_accepted_fact_depends_on_it(knowledge):
    _accepted_fact(knowledge)

    with pytest.raises(sqlite3.IntegrityError, match="accepted_facts_depend_on_entity"):
        knowledge.entities.connection.execute(
            "UPDATE entities SET review_status='pending' WHERE id=?",
            (knowledge.person_id,),
        )
