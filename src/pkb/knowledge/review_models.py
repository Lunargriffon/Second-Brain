"""Low-level value objects for human-owned review state."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


READING_STATUSES = frozenset({"unread", "queued", "reading", "read", "archived", "ignored"})


class DocumentNotFoundError(ValueError):
    """Raised when a human operation targets a document that does not exist."""


@dataclass(frozen=True)
class ReadingState:
    document_id: int
    status: str
    priority: int | None
    reason: str | None
    last_reviewed: str | None
    user_note: str | None


def normalize_tag(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("tag must be a string")
    display = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()
    if not display:
        raise ValueError("tag cannot be empty")
    return display.casefold(), display

