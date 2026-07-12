"""Normalize archived Zhihu records into the shared knowledge contract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from pkb.knowledge.models import NormalizedDocument, SourceMembership
from pkb.knowledge.urls import canonicalize_url, platform_identity


_COLLECTION_FILE = re.compile(r"^zhihu-(.+)\.jsonl$")


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _string_urls(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


class ZhihuAdapter:
    source_name = "zhihu"

    def normalize(
        self, record: Mapping[str, object], *, raw_path: Path, raw_line: int
    ) -> NormalizedDocument:
        source_url = _text(record.get("url"))
        canonical_url = canonicalize_url(source_url)
        source_item_id = _text(record.get("id"))
        identity_key = platform_identity(canonical_url) or f"zhihu:{source_item_id}"
        match = _COLLECTION_FILE.match(raw_path.name)
        collection_id = match.group(1) if match else None

        return NormalizedDocument(
            identity_key=identity_key,
            canonical_url=canonical_url,
            title=_text(record.get("title")),
            author=_text(record.get("author")),
            plain_content=_text(record.get("content")),
            media_urls=_string_urls(record.get("images")),
            source_created_at=_text(record.get("created_at")) or None,
            membership=SourceMembership(
                source=self.source_name,
                source_item_id=source_item_id,
                collection_id=collection_id,
                source_url=source_url,
                raw_path=raw_path,
                raw_line=raw_line,
            ),
        )
