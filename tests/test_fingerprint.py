from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pkb.knowledge.fingerprint import normalized_content_hash, source_content_hash


def test_source_hash_ignores_acquisition_metadata():
    a = {"title": "T", "content": "C", "url": "u", "saved_at": "one"}
    b = {"title": "T", "content": "C", "url": "u", "saved_at": "two"}

    assert source_content_hash(a) == source_content_hash(b)


def test_source_hash_includes_aliases_and_source_creation_time():
    base = {"id": "1", "authorName": "A", "text": "C", "created_at": "2025-01-01"}

    assert source_content_hash(base) != source_content_hash({**base, "text": "D"})
    assert source_content_hash(base) != source_content_hash(
        {**base, "created_at": "2025-01-02"}
    )


def test_dict_key_order_does_not_affect_source_hash():
    a = {"title": "T", "links": [{"url": "u", "label": "L"}]}
    b = {"links": [{"label": "L", "url": "u"}], "title": "T"}

    assert source_content_hash(a) == source_content_hash(b)


def test_media_order_remains_meaningful():
    assert source_content_hash({"images": ["a", "b"]}) != source_content_hash(
        {"images": ["b", "a"]}
    )


def test_non_json_values_are_normalized_deterministically():
    first = {
        "links": {
            "path": Path("archive/item"),
            "seen": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "labels": {"beta", "alpha"},
        }
    }
    second = {
        "links": {
            "labels": {"alpha", "beta"},
            "seen": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "path": Path("archive/item"),
        }
    }

    assert source_content_hash(first) == source_content_hash(second)


def test_normalized_hash_changes_with_normalized_body():
    assert normalized_content_hash("T", "A", "C", "u", ()) != normalized_content_hash(
        "T", "A", "D", "u", ()
    )


def test_hashes_are_lowercase_sha256_hex():
    values = [
        source_content_hash({"title": "T"}),
        normalized_content_hash("T", "A", "C", "u", ("image",)),
    ]

    assert all(len(value) == 64 for value in values)
    assert all(value == value.lower() for value in values)
    assert all(set(value) <= set("0123456789abcdef") for value in values)
