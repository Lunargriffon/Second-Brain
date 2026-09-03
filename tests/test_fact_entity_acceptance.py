from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pkb.derive.jobs import JobQueue, LeaseOwnershipError
from pkb.knowledge.entity_repository import EntityRepository
from pkb.knowledge.fact_models import EntityObject, ScalarObject
from pkb.knowledge.fact_repository import FactRepository


@pytest.fixture
def knowledge(tmp_path):
    database = tmp_path / "knowledge.db"
    entities = EntityRepository(database)
    facts = FactRepository(database)
    yield database, entities, facts
    facts.close()
    entities.close()


def _accepted_entity(entities: EntityRepository, name: str, entity_type: str = "person") -> int:
    entity_id = entities.create_candidate(entity_type, name, actor="llm:test")
    entities.accept(entity_id, actor="human:test", reason="confirmed")
    return entity_id


def _text_version(facts: FactRepository, identity: str, content: str) -> tuple[int, int]:
    cursor = facts.connection.execute(
        """INSERT INTO documents
           (identity_key, source_type, title, plain_content, source_content_hash,
            normalized_content_hash, normalization_version, schema_version)
           VALUES (?, 'local', ?, ?, ?, ?, 1, 1)""",
        (identity, identity, content, f"source:{identity}", f"normalized:{identity}"),
    )
    document_id = int(cursor.lastrowid)
    cursor = facts.connection.execute(
        """INSERT INTO document_text_versions
           (document_id, source_content_hash, normalized_content_hash,
            normalization_version, plain_content)
           VALUES (?, ?, ?, 1, ?)""",
        (document_id, f"source:{identity}", f"normalized:{identity}", content),
    )
    facts.connection.commit()
    return document_id, int(cursor.lastrowid)


def _ground(
    facts: FactRepository, fact_id: int, text_version_id: int, excerpt: str
):
    return facts.add_evidence(
        fact_id,
        text_version_id,
        role="supports",
        start=0,
        end=len(excerpt),
        excerpt=excerpt,
    )


def test_acceptance_normal_extraction_and_publication(knowledge):
    _, entities, facts = knowledge
    person = _accepted_entity(entities, "Zhang San")
    organization = _accepted_entity(entities, "OpenAI", "organization")
    _, version = _text_version(facts, "normal", "Zhang San works at OpenAI")

    candidate = facts.create_candidate(
        person, "works_at", EntityObject(organization), actor="llm:fact-v1"
    )
    evidence = _ground(facts, candidate.id, version, "Zhang San")
    published = facts.accept(candidate.id, actor="human:test", reason="verified")

    assert (published.review_status, published.knowledge_status) == ("accepted", "active")
    assert facts.evidence(published.id, valid_only=True) == (evidence,)


def test_acceptance_conflict_detection_without_auto_adjudication(knowledge):
    _, entities, facts = knowledge
    subject = _accepted_entity(entities, "Project Atlas", "project")
    evidence_ids = []
    fact_ids = []
    for ordinal, status in enumerate(("approved", "rejected"), start=1):
        _, version = _text_version(facts, f"conflict:{ordinal}", status)
        fact = facts.create_candidate(
            subject, "status", ScalarObject("string", status), actor="llm:fact-v1"
        )
        evidence_ids.append(_ground(facts, fact.id, version, status).id)
        fact_ids.append(facts.accept(fact.id, actor="human:test", reason="source verified").id)

    relation = facts.create_relation_candidate(
        fact_ids[0],
        fact_ids[1],
        "conflicts_with",
        explanation="sources disagree",
        evidence_ids=evidence_ids,
        actor="deterministic:conflict-v1",
    )

    assert relation.review_status == "pending"
    assert relation.deterministic_validation_status == "passed"
    assert {facts.get(fact_id).knowledge_status for fact_id in fact_ids} == {"disputed"}


def test_acceptance_supersession_preserves_history(knowledge):
    _, entities, facts = knowledge
    subject = _accepted_entity(entities, "Zhang San")
    organization = _accepted_entity(entities, "New Company", "organization")
    accepted = []
    evidence_ids = []
    for ordinal, start in enumerate(("2025-01-01", "2026-01-01"), start=1):
        _, version = _text_version(facts, f"supersede:{ordinal}", "employment record")
        fact = facts.create_candidate(
            subject,
            "works_at",
            EntityObject(organization),
            actor="llm:fact-v1",
            valid_from=start,
        )
        evidence_ids.append(_ground(facts, fact.id, version, "employment record").id)
        accepted.append(facts.accept(fact.id, actor="human:test", reason="verified"))

    newer, older = accepted[1], accepted[0]
    relation = facts.supersede(
        newer.id,
        older.id,
        explanation="later effective date",
        evidence_ids=[evidence_ids[1], evidence_ids[0]],
        actor="human:test",
        reason="time ordered",
    )

    assert relation.knowledge_status == "active"
    assert facts.get(older.id).knowledge_status == "superseded"
    assert facts.connection.execute("SELECT count(*) FROM facts").fetchone()[0] == 2


def test_acceptance_entity_merge_and_undo_preserve_fact_ids(knowledge):
    _, entities, facts = knowledge
    duplicate = _accepted_entity(entities, "Zhang San")
    canonical = _accepted_entity(entities, "张三")
    _, version = _text_version(facts, "merge", "Zhang San is a researcher")
    fact = facts.create_candidate(
        duplicate, "role", ScalarObject("string", "researcher"), actor="llm:fact-v1"
    )
    _ground(facts, fact.id, version, "Zhang San")
    fact = facts.accept(fact.id, actor="human:test", reason="verified")

    merge_event = entities.merge(
        duplicate, canonical, actor="human:test", reason="same person"
    )
    assert entities.resolve_canonical(duplicate) == canonical
    assert facts.get(fact.id).subject_entity_id == duplicate

    entities.undo_merge(merge_event, actor="human:test", reason="mistaken merge")
    assert entities.resolve_canonical(duplicate) == duplicate
    assert facts.get(fact.id).subject_entity_id == duplicate


def test_acceptance_evidence_invalidation_and_rebuild(knowledge):
    _, entities, facts = knowledge
    subject = _accepted_entity(entities, "Zhang San")
    _, old_version = _text_version(facts, "stale:old", "Zhang San is active")
    fact = facts.create_candidate(
        subject, "status", ScalarObject("string", "active"), actor="llm:fact-v1"
    )
    old_evidence = _ground(facts, fact.id, old_version, "Zhang San")
    facts.accept(fact.id, actor="human:test", reason="verified")

    affected = facts.invalidate_evidence(
        old_evidence.id, actor="system:source-update", reason="source changed"
    )
    assert affected == (fact.id,)
    assert facts.get(fact.id).review_status == "pending"

    _, new_version = _text_version(facts, "stale:new", "Zhang San remains active")
    _ground(facts, fact.id, new_version, "Zhang San")
    rebuilt = facts.accept(fact.id, actor="human:test", reason="replacement verified")
    assert (rebuilt.review_status, rebuilt.knowledge_status) == ("accepted", "active")
    assert len(facts.evidence(fact.id)) == 2
    assert len(facts.evidence(fact.id, valid_only=True)) == 1


def test_acceptance_multiple_sources_share_one_fact(knowledge):
    _, entities, facts = knowledge
    subject = _accepted_entity(entities, "Project Atlas", "project")
    first = facts.find_or_create_candidate(
        subject, "status", ScalarObject("string", "active"), actor="llm:source-a"
    )
    _, first_version = _text_version(facts, "source:a", "Project Atlas is active")
    _ground(facts, first.id, first_version, "Project Atlas")

    second = facts.find_or_create_candidate(
        subject, "status", ScalarObject("string", "active"), actor="llm:source-b"
    )
    _, second_version = _text_version(facts, "source:b", "Project Atlas remains active")
    _ground(facts, second.id, second_version, "Project Atlas")
    published = facts.accept(first.id, actor="human:test", reason="corroborated")

    assert first.id == second.id == published.id
    assert len(facts.evidence(published.id, valid_only=True)) == 2
    assert facts.connection.execute("SELECT count(*) FROM facts").fetchone()[0] == 1


def test_acceptance_lost_job_lease_cannot_publish(knowledge):
    database, entities, facts = knowledge
    subject = _accepted_entity(entities, "Project Atlas", "project")
    document_id, version = _text_version(facts, "lease", "Project Atlas is active")
    candidate = facts.create_candidate(
        subject, "status", ScalarObject("string", "active"), actor="llm:fact-v1"
    )
    _ground(facts, candidate.id, version, "Project Atlas")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with JobQueue(database) as queue:
        job_id = queue.enqueue("article", document_id, "lease-input", "fact-v1")
        queue.claim("worker-a", now=now, lease=timedelta(minutes=1))
        reclaimed = queue.claim(
            "worker-b", now=now + timedelta(minutes=2), lease=timedelta(minutes=15)
        )
        assert reclaimed is not None and reclaimed.worker_id == "worker-b"

        with pytest.raises(LeaseOwnershipError):
            queue.publish_and_succeed(
                job_id,
                "worker-a",
                lambda connection: FactRepository.from_connection(
                    connection
                ).accept_in_transaction(
                    candidate.id, actor="human:test", reason="lease-owned publish"
                ),
                now=now + timedelta(minutes=2),
            )

    assert facts.get(candidate.id).review_status == "pending"
