from __future__ import annotations

import sqlite3

import pytest

from pkb.knowledge.entity_repository import (
    EntityMergeCycleError,
    EntityRepository,
    InvalidEntityTransition,
)


@pytest.fixture
def entity_repository(tmp_path):
    repository = EntityRepository(tmp_path / "knowledge.db")
    yield repository
    repository.close()


def accepted_entity(repository, entity_type: str, name: str) -> int:
    entity_id = repository.create_candidate(entity_type, name, actor="test")
    repository.accept(entity_id, actor="human:test", reason="identity confirmed")
    return entity_id


@pytest.fixture
def text_version(entity_repository):
    cursor = entity_repository.connection.execute(
        """INSERT INTO documents
           (identity_key, source_type, title, plain_content, source_content_hash,
            normalized_content_hash, normalization_version, schema_version)
           VALUES ('local:mention:1', 'local', 'mention', '张三在这里', 'source-1',
                   'normalized-1', 1, 1)"""
    )
    document_id = int(cursor.lastrowid)
    cursor = entity_repository.connection.execute(
        """INSERT INTO document_text_versions
           (document_id, source_content_hash, normalized_content_hash,
            normalization_version, plain_content)
           VALUES (?, 'source-1', 'normalized-1', 1, '张三在这里')""",
        (document_id,),
    )
    entity_repository.connection.commit()
    return cursor.lastrowid


def test_entity_candidate_must_be_accepted_before_fact_use(entity_repository):
    entity_id = entity_repository.create_candidate("person", "张三", actor="test")
    assert entity_repository.get(entity_id).review_status == "pending"
    entity_repository.accept(entity_id, actor="human:test", reason="identity confirmed")
    assert entity_repository.get(entity_id).review_status == "accepted"


def test_rejected_entity_cannot_be_reaccepted_in_place(entity_repository):
    entity_id = entity_repository.create_candidate("person", "重名", actor="test")
    entity_repository.reject(entity_id, actor="human:test", reason="wrong person")
    with pytest.raises(InvalidEntityTransition):
        entity_repository.accept(entity_id, actor="human:test", reason="changed mind")


def test_entity_names_are_nfkc_whitespace_collapsed_and_casefolded(entity_repository):
    entity_id = entity_repository.create_candidate("organization", "  Ｏpen   AI  ", actor="test")
    entity = entity_repository.get(entity_id)
    assert entity.canonical_name == "Ｏpen AI"
    assert entity.normalized_name == "open ai"


def test_every_entity_mutation_is_audited(entity_repository):
    entity_id = entity_repository.create_candidate("person", "甲", actor="llm:extractor")
    entity_repository.accept(entity_id, actor="human:reviewer", reason="confirmed")
    events = entity_repository.connection.execute(
        "SELECT event_type, actor_type FROM knowledge_events WHERE object_type='entity' AND object_id=? ORDER BY id",
        (entity_id,),
    ).fetchall()
    assert [(row[0], row[1]) for row in events] == [
        ("created", "llm_candidate"),
        ("accepted", "human"),
    ]


def test_same_accepted_strong_identifier_cannot_link_two_entities(entity_repository):
    first = accepted_entity(entity_repository, "person", "甲")
    second = accepted_entity(entity_repository, "person", "乙")
    entity_repository.add_alias(first, "email", "A@example.com", strong=True, accept=True)
    with pytest.raises(sqlite3.IntegrityError):
        entity_repository.add_alias(second, "email", "a@example.com", strong=True, accept=True)


def test_mention_binds_exact_text_version(entity_repository, text_version):
    entity_id = accepted_entity(entity_repository, "person", "张三")
    mention = entity_repository.add_mention(
        text_version, "张三", 0, 2, "person", entity_id=entity_id,
        method="strong_identifier", accept=True,
    )
    assert mention.entity_id == entity_id
    row = entity_repository.connection.execute(
        "SELECT normalized_content_hash, normalization_version FROM entity_mentions WHERE id=?",
        (mention.id,),
    ).fetchone()
    assert tuple(row) == ("normalized-1", 1)


def test_non_strong_mention_cannot_auto_accept(entity_repository, text_version):
    entity_id = accepted_entity(entity_repository, "person", "张三")
    mention = entity_repository.add_mention(
        text_version, "张三", 0, 2, "person", entity_id=entity_id,
        method="llm", accept=True,
    )
    assert mention.review_status == "pending"


def test_resolve_canonical_returns_unmerged_entity(entity_repository):
    entity_id = accepted_entity(entity_repository, "person", "张三")
    assert entity_repository.resolve_canonical(entity_id) == entity_id


def test_merge_and_undo_resolve_canonical_without_rewriting_fact_ids(entity_repository):
    winner = accepted_entity(entity_repository, "organization", "OpenAI")
    loser = accepted_entity(entity_repository, "organization", "Open AI Inc.")
    subject = accepted_entity(entity_repository, "person", "Zhang San")
    cursor = entity_repository.connection.execute(
        """INSERT INTO facts
           (fact_key, subject_entity_id, predicate, object_entity_id, object_type,
            object_normalized_text)
           VALUES (?, ?, 'works_at', ?, 'entity', ?)""",
        ("f" * 64, subject, loser, str(loser)),
    )
    fact_id = int(cursor.lastrowid)
    entity_repository.connection.commit()

    event_id = entity_repository.merge(
        loser, winner, actor="human:test", reason="same organization"
    )
    assert entity_repository.resolve_canonical(loser) == winner
    assert entity_repository.connection.execute(
        "SELECT object_entity_id FROM facts WHERE id=?", (fact_id,)
    ).fetchone()[0] == loser

    entity_repository.undo_merge(
        event_id, actor="human:test", reason="incorrect merge"
    )
    assert entity_repository.resolve_canonical(loser) == loser
    assert entity_repository.connection.execute(
        "SELECT object_entity_id FROM facts WHERE id=?", (fact_id,)
    ).fetchone()[0] == loser


def test_merge_rejects_cycle(entity_repository):
    first = accepted_entity(entity_repository, "person", "First")
    second = accepted_entity(entity_repository, "person", "Second")
    entity_repository.merge(second, first, actor="human:test", reason="candidate")

    with pytest.raises(EntityMergeCycleError):
        entity_repository.merge(first, second, actor="human:test", reason="would cycle")


def test_undo_merge_requires_current_unreversed_event(entity_repository):
    winner = accepted_entity(entity_repository, "organization", "Winner")
    loser = accepted_entity(entity_repository, "organization", "Loser")
    event_id = entity_repository.merge(loser, winner, actor="human:test", reason="same")
    entity_repository.undo_merge(event_id, actor="human:test", reason="mistake")

    with pytest.raises(InvalidEntityTransition, match="already reversed"):
        entity_repository.undo_merge(event_id, actor="human:test", reason="again")


def test_merge_and_undo_are_audited(entity_repository):
    winner = accepted_entity(entity_repository, "organization", "Winner")
    loser = accepted_entity(entity_repository, "organization", "Loser")
    event_id = entity_repository.merge(loser, winner, actor="human:test", reason="same")
    entity_repository.undo_merge(event_id, actor="human:test", reason="mistake")

    merge_rows = entity_repository.connection.execute(
        """SELECT action, loser_entity_id, winner_entity_id, reverses_event_id
           FROM entity_merge_events ORDER BY id"""
    ).fetchall()
    assert [tuple(row) for row in merge_rows] == [
        ("merge", loser, winner, None),
        ("undo", loser, winner, event_id),
    ]
    audit_types = entity_repository.connection.execute(
        """SELECT event_type FROM knowledge_events
           WHERE object_type='entity_merge' ORDER BY id"""
    ).fetchall()
    assert [row[0] for row in audit_types] == ["merged", "merge_undone"]
