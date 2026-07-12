from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceMembership:
    source: str
    source_item_id: str
    collection_id: str | None
    source_url: str
    raw_path: Path
    raw_line: int


@dataclass(frozen=True)
class NormalizedDocument:
    identity_key: str
    canonical_url: str
    title: str
    author: str
    plain_content: str
    media_urls: tuple[str, ...]
    source_created_at: str | None
    membership: SourceMembership
