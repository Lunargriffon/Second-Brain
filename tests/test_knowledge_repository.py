from dataclasses import replace
from pathlib import Path

import pytest

from pkb.knowledge.models import NormalizedDocument, SourceMembership
from pkb.knowledge.repository import KnowledgeRepository, MergeConflictError


def document(*, identity="zhihu:answer:1", source="zhihu", item="1", collection="c", url="https://www.zhihu.com/question/9/answer/1", content="body"):
    return NormalizedDocument(
        identity_key=identity,
        canonical_url=url,
        title="title",
        author="author",
        plain_content=content,
        media_urls=("https://img.test/a.jpg",),
        source_created_at="2024-01-01T00:00:00Z",
        membership=SourceMembership(source, item, collection, url, Path(f"{source}.jsonl"), 1),
    )


@pytest.fixture
def repository(tmp_path):
    with KnowledgeRepository(tmp_path / "knowledge.db") as value:
        yield value


def test_upsert_keeps_one_document_with_two_memberships(repository):
    first = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1)
    x_doc = document(source="x", item="tweet-1", collection=None, url="https://www.zhihu.com/question/9/answer/1?utm_source=x")
    second = repository.upsert_document(x_doc, source_hash="s2", normalized_hash="n1", normalization_version=1)

    assert first.created is True
    assert second.document_id == first.document_id
    assert repository.count_documents() == 1
    assert repository.count_memberships(first.document_id) == 2
    assert repository.memberships(first.document_id)[1]["collection_id"] == ""
    assert repository.count_media(first.document_id) == 1


def test_upsert_reports_hash_changes(repository):
    first = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1)
    unchanged = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1)
    changed = repository.upsert_document(document(content="edited"), source_hash="s2", normalized_hash="n2", normalization_version=1)

    assert (first.created, first.source_changed, first.normalized_changed) == (True, True, True)
    assert (unchanged.created, unchanged.source_changed, unchanged.normalized_changed) == (False, False, False)
    assert (changed.created, changed.source_changed, changed.normalized_changed) == (False, True, True)


def test_identityless_documents_may_match_exact_source_hash(repository):
    first = repository.upsert_document(document(identity="url:first", url="https://example.test/a"), source_hash="same", normalized_hash="n1", normalization_version=1)
    second = repository.upsert_document(document(identity="url:second", source="x", item="2", url="https://example.test/b"), source_hash="same", normalized_hash="n1", normalization_version=1)
    assert second.document_id == first.document_id


def test_merge_moves_owned_records_and_audits_alias(repository):
    survivor = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1).document_id
    duplicate = repository.upsert_document(document(identity="x:tweet:2", source="x", item="2", url="https://x.com/a/status/2"), source_hash="s2", normalized_hash="n2", normalization_version=1).document_id
    repository.connection.execute("INSERT INTO tags(normalized_name, display_name) VALUES ('manual', 'Manual')")
    repository.connection.execute("INSERT INTO document_tags(document_id, tag_id, origin) VALUES (?, 1, 'user')", (duplicate,))
    repository.connection.commit()

    repository.merge_documents(survivor, duplicate, reason="same content")

    assert repository.count_documents() == 1
    assert repository.count_memberships(survivor) == 2
    assert repository.resolve_document_id(duplicate) == survivor
    assert repository.count_merges() == 1
    assert repository.connection.execute("SELECT document_id FROM document_tags").fetchone()[0] == survivor


def test_merge_conflicting_human_notes_rolls_back(repository):
    survivor = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1).document_id
    duplicate = repository.upsert_document(document(identity="x:2", source="x", item="2", url="https://x.com/a/status/2"), source_hash="s2", normalized_hash="n2", normalization_version=1).document_id
    repository.set_reading_state(survivor, status="read", user_note_ref="one.md")
    repository.set_reading_state(duplicate, status="read", user_note_ref="two.md")

    with pytest.raises(MergeConflictError, match="notes"):
        repository.merge_documents(survivor, duplicate, reason="same")
    assert repository.count_documents() == 2
    assert repository.count_merges() == 0


def test_merge_default_rejects_conflicting_explicit_reading_state(repository):
    survivor = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1).document_id
    duplicate = repository.upsert_document(document(identity="x:2", source="x", item="2", url="https://x.com/a/status/2"), source_hash="s2", normalized_hash="n2", normalization_version=1).document_id
    repository.set_reading_state(survivor, status="read", manual_priority=5, priority_reason="important", last_reviewed_at="2024-01-01")
    repository.set_reading_state(duplicate, status="unread", manual_priority=2, priority_reason="later", last_reviewed_at="2024-02-01")

    with pytest.raises(MergeConflictError, match="reading state"):
        repository.merge_documents(survivor, duplicate, reason="same")
    assert repository.count_documents() == 2


@pytest.mark.parametrize("policy, expected", [("survivor", "read"), ("newest_reviewed", "unread")])
def test_merge_explicit_reading_state_resolution_is_audited(repository, policy, expected):
    survivor = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1).document_id
    duplicate = repository.upsert_document(document(identity="x:2", source="x", item="2", url="https://x.com/a/status/2"), source_hash="s2", normalized_hash="n2", normalization_version=1).document_id
    repository.set_reading_state(survivor, status="read", last_reviewed_at="2024-01-01")
    repository.set_reading_state(duplicate, status="unread", last_reviewed_at="2024-02-01")

    repository.merge_documents(survivor, duplicate, reason="same", reading_state_policy=policy)

    assert repository.reading_state(survivor)["status"] == expected
    audit = repository.latest_merge()
    assert audit["reading_state_policy"] == policy
    assert '"survivor"' in audit["metadata_json"] and '"duplicate"' in audit["metadata_json"]


def test_merge_rejects_unsafe_child_records_without_writing_anything(repository):
    survivor = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1).document_id
    duplicate = repository.upsert_document(document(identity="x:tweet:2", source="x", item="2", url="https://x.com/a/status/2"), source_hash="s2", normalized_hash="n2", normalization_version=1).document_id
    other = repository.upsert_document(document(identity="x:tweet:3", source="x", item="3", url="https://x.com/a/status/3"), source_hash="s3", normalized_hash="n3", normalization_version=1).document_id
    db = repository.connection
    db.execute("INSERT INTO derivations(id, document_id, kind, payload_json, input_hash, source_content_hash, normalized_content_hash, normalization_version, schema_version, prompt_version, status) VALUES ('d1', ?, 'summary', '{}', 'ih', 's2', 'n2', 1, 1, 'p1', 'done')", (duplicate,))
    db.execute("INSERT INTO tags(normalized_name, display_name) VALUES ('ai', 'AI')")
    db.execute("INSERT INTO document_tags(document_id, tag_id, origin, derivation_id) VALUES (?, 1, 'ai', 'd1')", (duplicate,))
    db.execute("INSERT INTO topics(normalized_name, display_name) VALUES ('topic', 'Topic')")
    db.execute("INSERT INTO document_topics(document_id, topic_id, origin, derivation_id) VALUES (?, 1, 'ai', 'd1')", (duplicate,))
    db.execute("INSERT INTO relations(source_document_id, target_document_id, relation_type, evidence, derivation_id) VALUES (?, ?, 'related', 'why', 'd1')", (duplicate, other))
    job = db.execute("INSERT INTO jobs(job_type, document_id, input_hash, pipeline_version, status) VALUES ('derive', ?, 'jhash', 'v1', 'failed')", (duplicate,)).lastrowid
    db.execute("INSERT INTO job_events(job_id, event_type) VALUES (?, 'failed')", (job,))
    db.execute("INSERT INTO document_identity_aliases(alias_identity_key, canonical_document_id) VALUES ('legacy:2', ?)", (duplicate,))
    db.commit()

    with pytest.raises(MergeConflictError, match="derived or queued"):
        repository.merge_documents(survivor, duplicate, reason="same")

    for table in ("derivations", "document_tags", "document_topics", "jobs"):
        assert db.execute(f"SELECT document_id FROM {table}").fetchone()[0] == duplicate
    assert db.execute("SELECT source_document_id FROM relations").fetchone()[0] == duplicate
    assert db.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1
    assert repository.count_documents() == 3
    assert repository.count_merges() == 0


def test_merged_document_ids_are_never_reused(repository):
    survivor = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1).document_id
    duplicate = repository.upsert_document(document(identity="x:2", source="x", item="2", url="https://x.com/a/status/2"), source_hash="s2", normalized_hash="n2", normalization_version=1).document_id
    repository.merge_documents(survivor, duplicate, reason="same")

    created = repository.upsert_document(document(identity="x:3", source="x", item="3", url="https://x.com/a/status/3"), source_hash="s3", normalized_hash="n3", normalization_version=1)
    assert created.document_id > duplicate


@pytest.mark.parametrize("first_source", ["x", "zhihu"])
def test_zhihu_core_fields_win_regardless_of_ingest_order(repository, first_source):
    zhihu = document(source="zhihu", item="1", content="authoritative")
    x = replace(document(source="x", item="tweet", content="bookmark copy"), title="bookmark")
    ordered = (x, zhihu) if first_source == "x" else (zhihu, x)
    hashes = {"x": ("sx", "nx"), "zhihu": ("sz", "nz")}
    for value in ordered:
        source_hash, normalized_hash = hashes[value.membership.source]
        result = repository.upsert_document(value, source_hash=source_hash, normalized_hash=normalized_hash, normalization_version=1)

    row = repository.document(result.document_id)
    assert row["plain_content"] == "authoritative"
    assert row["title"] == "title"
    assert row["source_content_hash"] == "sz"
    assert row["normalized_content_hash"] == "nz"
    assert repository.count_memberships(result.document_id) == 2


def test_upsert_refuses_to_reassign_membership_evidence(repository):
    repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1)
    conflicting = document(identity="zhihu:answer:99", url="https://www.zhihu.com/question/9/answer/99")

    with pytest.raises(MergeConflictError, match="membership"):
        repository.upsert_document(conflicting, source_hash="s2", normalized_hash="n2", normalization_version=1)
    assert repository.count_documents() == 1


def test_merge_refuses_conflicting_media_metadata_and_rolls_back(repository):
    survivor = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1).document_id
    duplicate = repository.upsert_document(document(identity="x:2", source="x", item="2", url="https://x.com/a/status/2"), source_hash="s2", normalized_hash="n2", normalization_version=1).document_id
    repository.connection.execute("UPDATE media SET local_path='different.jpg' WHERE document_id=?", (duplicate,))
    repository.connection.commit()

    with pytest.raises(MergeConflictError, match="media"):
        repository.merge_documents(survivor, duplicate, reason="same")
    assert repository.count_documents() == 2
    assert repository.count_merges() == 0


def test_old_identity_upsert_resolves_to_merge_survivor(repository):
    survivor = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1).document_id
    old_doc = document(identity="x:old", source="x", item="old", url="https://x.com/a/status/old")
    duplicate = repository.upsert_document(old_doc, source_hash="s2", normalized_hash="n2", normalization_version=1).document_id
    repository.merge_documents(survivor, duplicate, reason="same")

    result = repository.upsert_document(old_doc, source_hash="s2", normalized_hash="n2", normalization_version=1)
    assert result.document_id == survivor
    assert repository.count_documents() == 1


def test_upsert_rejects_disagreeing_identity_and_url_candidates_transactionally(repository):
    identity_doc = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1)
    url_doc = repository.upsert_document(document(identity="x:url-owner", source="x", item="url-owner", url="https://x.com/url-owner"), source_hash="s2", normalized_hash="n2", normalization_version=1)
    aliases_before = repository.connection.execute("SELECT COUNT(*) FROM document_url_aliases").fetchone()[0]
    memberships_before = repository.connection.execute("SELECT COUNT(*) FROM source_memberships").fetchone()[0]
    conflicting = document(identity="zhihu:answer:1", source="x", item="conflict", url="https://x.com/url-owner")

    with pytest.raises(MergeConflictError, match="identity candidates"):
        repository.upsert_document(conflicting, source_hash="s3", normalized_hash="n3", normalization_version=1)

    assert repository.count_documents() == 2
    assert repository.connection.execute("SELECT COUNT(*) FROM document_url_aliases").fetchone()[0] == aliases_before
    assert repository.connection.execute("SELECT COUNT(*) FROM source_memberships").fetchone()[0] == memberships_before
    assert repository.document(identity_doc.document_id)["source_content_hash"] == "s1"
    assert repository.document(url_doc.document_id)["source_content_hash"] == "s2"


def test_empty_urls_do_not_create_a_global_slash_alias(repository):
    one = repository.upsert_document(document(identity="local:1", url=""), source_hash="s1", normalized_hash="n1", normalization_version=1)
    two = repository.upsert_document(document(identity="local:2", item="2", url=""), source_hash="s2", normalized_hash="n2", normalization_version=1)

    assert one.document_id != two.document_id
    assert repository.count_documents() == 2
    assert repository.connection.execute("SELECT COUNT(*) FROM document_url_aliases").fetchone()[0] == 0


def test_identical_reading_state_keeps_nonempty_note_over_newer_review(repository):
    survivor = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1).document_id
    duplicate = repository.upsert_document(document(identity="x:note", source="x", item="note", url="https://x.com/note"), source_hash="s2", normalized_hash="n2", normalization_version=1).document_id
    repository.set_reading_state(survivor, status="read", user_note_ref="note.md", last_reviewed_at="2024-01-01")
    repository.set_reading_state(duplicate, status="read", last_reviewed_at="2024-02-01")

    repository.merge_documents(survivor, duplicate, reason="same")

    state = repository.reading_state(survivor)
    assert state["user_note_ref"] == "note.md"
    assert state["last_reviewed_at"] == "2024-02-01"


def test_merge_rejects_url_alias_metadata_conflict(repository):
    survivor = repository.upsert_document(document(), source_hash="s1", normalized_hash="n1", normalization_version=1).document_id
    duplicate = repository.upsert_document(document(identity="x:alias", source="x", item="alias", url="https://x.com/alias"), source_hash="s2", normalized_hash="n2", normalization_version=1).document_id
    db = repository.connection
    survivor_url = db.execute("SELECT canonical_url FROM documents WHERE id=?", (survivor,)).fetchone()[0]
    db.execute("DELETE FROM document_url_aliases WHERE document_id=?", (survivor,))
    db.execute("UPDATE document_url_aliases SET url=?, observed_url=?, source='x' WHERE document_id=?", (survivor_url, survivor_url + "?source=x", duplicate))
    db.commit()

    with pytest.raises(MergeConflictError, match="URL alias"):
        repository.merge_documents(survivor, duplicate, reason="same")
    assert repository.count_documents() == 2
    assert repository.count_merges() == 0
