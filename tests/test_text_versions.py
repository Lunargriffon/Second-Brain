from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pkb.knowledge.models import NormalizedDocument, SourceMembership
from pkb.knowledge.repository import KnowledgeRepository
from pkb.knowledge.text_versions import GroundingError, normalize_plain_text, validate_span


def _document(content: str = "Ａ\r\n咖啡") -> NormalizedDocument:
    return NormalizedDocument(
        identity_key="local:text-version:1",
        canonical_url="https://example.test/text-version/1",
        title="title",
        author="author",
        plain_content=content,
        media_urls=(),
        source_created_at="2026-07-19T00:00:00Z",
        membership=SourceMembership(
            "local", "text-version-1", "tests", "https://example.test/text-version/1",
            Path("fixture.jsonl"), 1,
        ),
    )


def test_normalized_text_uses_nfkc_and_lf():
    assert normalize_plain_text("Ａ\r\n咖啡") == "A\n咖啡"


def test_offsets_are_unicode_code_points_and_half_open():
    text = "甲😄乙"
    assert validate_span(text, 1, 2, "😄") == "😄"


def test_grounding_rejects_wrong_repeated_excerpt_offset():
    with pytest.raises(GroundingError, match="exact span"):
        validate_span("重复，重复", 0, 2, "重复，")


def test_document_upsert_preserves_old_text_version(tmp_path):
    with KnowledgeRepository(tmp_path / "knowledge.db") as repository:
        original = _document()
        first = repository.upsert_document(
            original, source_hash="s1", normalized_hash="n1", normalization_version=1,
        )
        changed = replace(original, plain_content="新正文")
        repository.upsert_document(
            changed, source_hash="s2", normalized_hash="n2", normalization_version=2,
        )
        rows = repository.connection.execute(
            "SELECT normalized_content_hash, plain_content, invalidated_at "
            "FROM document_text_versions WHERE document_id=? ORDER BY id",
            (first.document_id,),
        ).fetchall()

    assert [(row[0], row[1]) for row in rows] == [("n1", original.plain_content), ("n2", "新正文")]
    assert rows[0][2] is not None and rows[1][2] is None


def test_unchanged_upsert_does_not_duplicate_text_version(tmp_path):
    with KnowledgeRepository(tmp_path / "knowledge.db") as repository:
        value = _document()
        first = repository.upsert_document(
            value, source_hash="s1", normalized_hash="n1", normalization_version=1,
        )
        repository.upsert_document(
            value, source_hash="s1", normalized_hash="n1", normalization_version=1,
        )
        count = repository.connection.execute(
            "SELECT count(*) FROM document_text_versions WHERE document_id=?",
            (first.document_id,),
        ).fetchone()[0]

    assert count == 1
