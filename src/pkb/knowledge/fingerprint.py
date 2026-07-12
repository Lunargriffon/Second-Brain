"""Stable fingerprints for source records and normalized knowledge content."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _canonical(value: Any) -> Any:
    """Convert supported values to deterministic, JSON-compatible data.

    Lists and tuples retain their order because media and link order can carry
    meaning. Unordered sets are sorted by their canonical JSON representation.
    Unknown objects are rejected instead of relying on an unstable ``repr``.
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("non-finite floats cannot be fingerprinted")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("fingerprinted mappings must have string keys")
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        items = [_canonical(item) for item in value]
        return sorted(items, key=_stable_json)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item) for item in value]
    if isinstance(value, Path):
        return {"$path": value.as_posix()}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, (bytes, bytearray)):
        return {"$bytes_hex": bytes(value).hex()}
    raise TypeError(f"unsupported fingerprint value: {type(value).__qualname__}")


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash(value: Any) -> str:
    payload = _stable_json(_canonical(value)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_content_hash(record: Mapping[str, object]) -> str:
    """Hash stable knowledge fields while excluding acquisition metadata."""

    knowledge = {
        "id": record.get("id"),
        "title": record.get("title"),
        "author": record.get("author"),
        "authorName": record.get("authorName"),
        "content": record.get("content"),
        "text": record.get("text"),
        "url": record.get("url"),
        "links": record.get("links"),
        "images": record.get("images"),
        "media": record.get("media"),
        "source_created_at": record.get("source_created_at"),
        "created_at": record.get("created_at"),
        "createdAt": record.get("createdAt"),
        "created_time": record.get("created_time"),
    }
    return _hash(knowledge)


def normalized_content_hash(
    title: str,
    author: str,
    plain_content: str,
    canonical_url: str,
    media_urls: Sequence[str],
) -> str:
    """Hash the explicit normalized document fields."""

    return _hash(
        {
            "title": title,
            "author": author,
            "plain_content": plain_content,
            "canonical_url": canonical_url,
            "media_urls": media_urls,
        }
    )
