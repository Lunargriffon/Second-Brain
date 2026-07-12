"""Normalize archived X bookmarks without performing network resolution."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from pkb.knowledge.models import NormalizedDocument, SourceMembership
from pkb.knowledge.urls import canonicalize_url, platform_identity


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _first_http_link(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, str):
            continue
        try:
            if urlsplit(item).scheme.lower() in {"http", "https"}:
                return item
        except ValueError:
            continue
    return None


def _string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _author(record: Mapping[str, object]) -> str:
    direct = _text(record.get("authorName"))
    if direct:
        return direct
    nested = record.get("author")
    return _text(nested.get("name")) if isinstance(nested, Mapping) else ""


class XBookmarkAdapter:
    source_name = "x"

    def normalize(
        self, record: Mapping[str, object], *, raw_path: Path, raw_line: int
    ) -> NormalizedDocument:
        tweet_url = _text(record.get("url"))
        target_url = _first_http_link(record.get("links")) or tweet_url
        canonical_url = canonicalize_url(target_url)
        source_item_id = _text(record.get("id")) or _text(record.get("tweetId"))
        recognized_identity = platform_identity(canonical_url)
        if not source_item_id and not recognized_identity:
            raise ValueError("stable source identity is required")
        identity_key = recognized_identity or f"x:{source_item_id}"
        content = _text(record.get("text"))

        return NormalizedDocument(
            identity_key=identity_key,
            canonical_url=canonical_url,
            title=content,
            author=_author(record),
            plain_content=content,
            media_urls=_string_items(record.get("media")),
            source_created_at=_text(record.get("postedAt")) or None,
            membership=SourceMembership(
                source=self.source_name,
                source_item_id=source_item_id,
                collection_id=None,
                source_url=tweet_url,
                raw_path=raw_path,
                raw_line=raw_line,
            ),
        )
