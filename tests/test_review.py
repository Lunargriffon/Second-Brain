from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pkb.knowledge.fingerprint import derivation_input_hash
from pkb.knowledge.repository import KnowledgeRepository
from pkb.review import DocumentNotFoundError, ReadingState


@pytest.fixture
def repository(tmp_path):
    with KnowledgeRepository(tmp_path / "knowledge.db") as repository:
        repository.connection.execute(
            """INSERT INTO documents
               (identity_key, source_content_hash, normalized_content_hash,
                normalization_version, schema_version)
               VALUES ('test:1', 'source', 'normalized', 1, 1)"""
        )
        repository.connection.commit()
        yield repository


def _derivation(repository: KnowledgeRepository, derivation_id: str = "d1") -> None:
    input_hash = derivation_input_hash("source", "normalized", 1)
    repository.connection.execute(
        """INSERT INTO derivations
           (id, document_id, kind, payload_json, input_hash, source_content_hash,
            normalized_content_hash, normalization_version, schema_version,
            prompt_version, status)
           VALUES (?, 1, 'article', '{}', ?, 'source', 'normalized',
                   1, 1, 'v1', 'accepted')""",
        (derivation_id, input_hash),
    )
    repository.connection.commit()


def test_ai_refresh_cannot_replace_human_state(repository):
    _derivation(repository)
    repository.set_reading_state(1, status="read", priority=5, reason="manual")
    repository.set_user_tags(1, ["核心"])

    repository.apply_ai_tags(1, derivation_id="d1", tags=[("核心", 0.2), ("学习", 0.9)])

    state = repository.get_reading_state(1)
    assert state == ReadingState(1, "read", 5, "manual", None, None)
    assert repository.get_user_tags(1) == ("核心",)
    assert repository.get_ai_tags(1) == (("学习", 0.9), ("核心", 0.2))


def test_reading_state_validates_enum_priority_and_supports_nullable_fields(repository):
    repository.set_reading_state(
        1, status="queued", priority=3, reason="soon",
        last_reviewed="2026-07-13T12:00:00Z", user_note="notes/1.md",
    )
    assert repository.get_reading_state(1) == ReadingState(
        1, "queued", 3, "soon", "2026-07-13T12:00:00Z", "notes/1.md"
    )
    repository.set_reading_state(1, priority=None, reason=None, user_note=None)
    assert repository.get_reading_state(1).priority is None
    for status in ("bad", "READ"):
        with pytest.raises(ValueError, match="status"):
            repository.set_reading_state(1, status=status)
    for priority in (0, 6, True):
        with pytest.raises(ValueError, match="priority"):
            repository.set_reading_state(1, priority=priority)
    assert repository.get_reading_state(1) == ReadingState(
        1, "queued", None, None, "2026-07-13T12:00:00Z", None
    )


def test_reading_state_is_a_frozen_value_and_missing_state_is_none(repository):
    assert repository.get_reading_state(1) is None
    repository.set_reading_state(1, status="unread")
    state = repository.get_reading_state(1)
    with pytest.raises(FrozenInstanceError):
        state.status = "read"


def test_tags_are_normalized_deduplicated_and_deterministically_ordered(repository):
    repository.set_user_tags(1, ["  Python ", "python", "ＡＩ", " ai ", "知识  管理"])
    assert repository.get_user_tags(1) == ("AI", "Python", "知识 管理")


def test_empty_user_tags_only_clear_user_origin(repository):
    _derivation(repository)
    repository.set_user_tags(1, ["人工"])
    repository.apply_ai_tags(1, derivation_id="d1", tags=[("机器", 0.8)])
    repository.set_user_tags(1, [])
    assert repository.get_user_tags(1) == ()
    assert repository.get_ai_tags(1) == (("机器", 0.8),)

    repository.set_user_tags(1, ["仍由用户拥有"])
    repository.apply_ai_tags(1, derivation_id="d1", tags=[])
    assert repository.get_user_tags(1) == ("仍由用户拥有",)
    assert repository.get_ai_tags(1) == ()


def test_ai_refresh_replaces_only_ai_rows_atomically(repository):
    _derivation(repository)
    repository.set_user_tags(1, ["same"])
    repository.apply_ai_tags(1, derivation_id="d1", tags=[("old", 0.7), ("same", 0.2)])
    with pytest.raises(ValueError, match="confidence"):
        repository.apply_ai_tags(1, derivation_id="d1", tags=[("new", 0.9), ("bad", 1.1)])
    assert repository.get_user_tags(1) == ("same",)
    assert repository.get_ai_tags(1) == (("old", 0.7), ("same", 0.2))


@pytest.mark.parametrize("defect", ["cross_document", "rejected", "stale", "wrong_kind"])
def test_ai_tags_reject_untrusted_derivation_without_changing_projection(repository, defect):
    _derivation(repository, "good")
    repository.apply_ai_tags(1, derivation_id="good", tags=[("existing", 0.8)])
    if defect == "cross_document":
        repository.connection.execute(
            """INSERT INTO documents(identity_key, source_content_hash,
               normalized_content_hash, normalization_version, schema_version)
               VALUES ('test:2', 's2', 'n2', 1, 1)"""
        )
        repository.connection.execute("UPDATE derivations SET document_id=2 WHERE id='good'")
    elif defect == "rejected":
        repository.connection.execute("UPDATE derivations SET status='rejected' WHERE id='good'")
    elif defect == "stale":
        repository.connection.execute("UPDATE derivations SET input_hash='stale' WHERE id='good'")
    else:
        repository.connection.execute("UPDATE derivations SET kind='relations' WHERE id='good'")
    repository.connection.commit()
    with pytest.raises(ValueError, match="derivation"):
        repository.apply_ai_tags(1, derivation_id="good", tags=[("replacement", 0.9)])
    raw = repository.connection.execute(
        """SELECT t.display_name FROM document_tags dt JOIN tags t ON t.id=dt.tag_id
           WHERE dt.document_id=1 AND dt.origin='ai'"""
    ).fetchall()
    assert [row[0] for row in raw] == ["existing"]
    assert repository.get_ai_tags(1) == ()  # stale provenance is never exposed as trusted AI state


def test_ai_cannot_change_display_name_shared_with_user_tag(repository):
    _derivation(repository)
    repository.set_user_tags(1, ["Human Display"])
    repository.apply_ai_tags(1, derivation_id="d1", tags=[("human display", 0.9)])
    assert repository.get_user_tags(1) == ("Human Display",)
    assert repository.get_ai_tags(1) == (("Human Display", 0.9),)


def test_duplicate_ai_tags_choose_highest_confidence_independent_of_input_order(repository):
    _derivation(repository)
    repository.apply_ai_tags(1, derivation_id="d1", tags=[("PYTHON", 0.9), ("python", 0.2)])
    assert repository.get_ai_tags(1) == (("PYTHON", 0.9),)


@pytest.mark.parametrize("operation", ["state", "user_tags", "ai_tags"])
def test_review_operations_report_missing_document(repository, operation):
    _derivation(repository)
    with pytest.raises(DocumentNotFoundError, match="999"):
        if operation == "state":
            repository.set_reading_state(999, status="read")
        elif operation == "user_tags":
            repository.set_user_tags(999, ["tag"])
        else:
            repository.apply_ai_tags(999, derivation_id="d1", tags=[("tag", 0.5)])
