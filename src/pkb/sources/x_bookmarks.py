"""Normalize archived X bookmarks without performing network resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping
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


class XBookmarkAdapter:
    source_name = "x"

    def normalize(
        self, record: Mapping[str, object], *, raw_path: Path, raw_line: int
    ) -> NormalizedDocument:
        tweet_url = _text(record.get("url"))
        target_url = _first_http_link(record.get("links")) or tweet_url
        canonical_url = canonicalize_url(target_url)
        source_item_id = _text(record.get("id"))
        identity_key = platform_identity(canonical_url) or f"x:{source_item_id}"
        content = _text(record.get("text"))

        return NormalizedDocument(
            identity_key=identity_key,
            canonical_url=canonical_url,
            title=content,
            author=_text(record.get("authorName")),
            plain_content=content,
            media_urls=(),
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
