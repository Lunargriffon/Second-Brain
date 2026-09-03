from __future__ import annotations

from types import SimpleNamespace

import pytest

from pkb.knowledge.entity_repository import EntityRepository
from pkb.knowledge.fact_models import EntityObject, ScalarObject, fact_key
from pkb.knowledge.fact_repository import FactPublicationError, FactRepository


@pytest.fixture
def fact_repository(tmp_path):
    repository = FactRepository(tmp_path / "knowledge.db")
    yield repository
    repository.close()


def accepted_entity(repository: FactRepository, entity_type: str, name: str) -> int:
    entity_repository = EntityRepository(repository.database)
    try:
        entity_id = entity_repository.create_candidate(entity_type, name, actor="test")
        entity_repository.accept(entity_id, actor="human:test", reason="confirmed")
        return entity_id
    finally:
        entity_repository.close()


@pytest.fixture
def entities(fact_repository):
    return SimpleNamespace(
        person=accepted_entity(fact_repository, "person", "Zhang San"),
        organization=accepted_entity(fact_repository, "organization", "OpenAI"),
    )


def text_version(repository: FactRepository, identity: str, content: str) -> SimpleNamespace:
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
    return SimpleNamespace(id=int(cursor.lastrowid), plain_content=content)


def test_fact_key_ignores_observation_and_evidence_metadata():
    first = fact_key(1, "works_at", EntityObject(2), valid_from="2026-01-01", valid_to=None)
    second = fact_key(1, " WORKS_AT ", EntityObject(2), valid_from="2026-01-01", valid_to=None)
    assert first == second
    assert len(first) == 64


def test_typed_scalar_changes_fact_identity():
    assert fact_key(1, "budget", ScalarObject("number", 100), None, None) != fact_key(
        1, "budget", ScalarObject("string", "100"), None, None
    )


def test_fact_publish_requires_valid_evidence_and_accepted_entities(
    fact_repository, entities
):
    version = text_version(fact_repository, "one", "Zhang works at OpenAI")
    candidate = fact_repository.create_candidate(
        entities.person, "works_at", EntityObject(entities.organization), actor="test"
    )
    with pytest.raises(FactPublicationError, match="valid evidence"):
        fact_repository.accept(candidate.id, actor="human:test", reason="confirmed")

    fact_repository.add_evidence(
        candidate.id, version.id, role="supports", start=0, end=5, excerpt="Zhang"
    )
    accepted = fact_repository.accept(candidate.id, actor="human:test", reason="confirmed")
    assert (accepted.review_status, accepted.knowledge_status) == ("accepted", "active")


def test_evidence_rejects_excerpt_that_is_not_exact_span(fact_repository, entities):
    version = text_version(fact_repository, "span", "Zhang works at OpenAI")
    candidate = fact_repository.create_candidate(
        entities.person, "works_at", EntityObject(entities.organization), actor="test"
    )
    with pytest.raises(ValueError, match="exact span"):
        fact_repository.add_evidence(
            candidate.id, version.id, role="supports", start=0, end=5, excerpt="Wrong"
        )


def test_second_source_adds_evidence_without_duplicate_fact(fact_repository, entities):
    versions = [
        text_version(fact_repository, "first", "OpenAI employs Zhang"),
        text_version(fact_repository, "second", "Zhang works at OpenAI"),
    ]
    first = fact_repository.create_candidate(
        entities.person, "works_at", EntityObject(entities.organization), actor="test"
    )
    fact_repository.add_evidence(
        first.id, versions[0].id, role="supports", start=0, end=6, excerpt="OpenAI"
    )
    fact_repository.accept(first.id, actor="human:test", reason="confirmed")

    same = fact_repository.find_or_create_candidate(
        entities.person, "works_at", EntityObject(entities.organization), actor="test"
    )
    fact_repository.add_evidence(
        same.id, versions[1].id, role="supports", start=0, end=5, excerpt="Zhang"
    )
    assert same.id == first.id
    assert len(fact_repository.evidence(first.id, valid_only=True)) == 2


def test_reject_and_reopen_follow_review_lifecycle(fact_repository, entities):
    candidate = fact_repository.create_candidate(
        entities.person, "budget", ScalarObject("number", 100), actor="test"
    )
    rejected = fact_repository.reject(candidate.id, actor="human:test", reason="wrong")
    assert rejected.review_status == "rejected"
    with pytest.raises(ValueError, match="cannot reopen"):
        fact_repository.reopen(candidate.id, actor="human:test", reason="retry")
