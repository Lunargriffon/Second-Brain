"""Human-owned reading state and deterministic daily review selection."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from pkb.derive.models import DerivationValidationError, validate_article_derivation
from pkb.derive.prompts import ARTICLE_PROMPT_VERSION
from pkb.knowledge.fingerprint import derivation_input_hash
from pkb.knowledge.repository import KnowledgeRepository
from pkb.wiki.renderer import stable_document_id

from pkb.knowledge.review_models import (
    DocumentNotFoundError,
    READING_STATUSES,
    ReadingState,
    normalize_tag,
)

MAX_DAILY_REVIEW_COUNT = 100
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class DailyReviewItem:
    document_id: int
    stable_id: str
    title: str
    source_url: str
    status: str
    manual_priority: int | None
    ai_reading_priority: int
    evergreen_score: int
    priority_reason: str
    primary_topic: str


@dataclass(frozen=True)
class _Candidate:
    item: DailyReviewItem
    age_days: int
    tie_breaker: str

    @property
    def base_rank(self) -> tuple[int, int, int, str, str]:
        return (
            self.item.ai_reading_priority,
            self.item.evergreen_score,
            self.age_days,
            self.tie_breaker,
            self.item.stable_id,
        )


def _review_date(value: str) -> Date:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise ValueError("date must use YYYY-MM-DD")
    try:
        return Date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must use YYYY-MM-DD") from exc


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class DailyReviewService:
    """Read candidates and apply explicit human review decisions."""

    def __init__(self, database: str | Path):
        self.repository = KnowledgeRepository(database)

    def __enter__(self) -> "DailyReviewService":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.repository.close()

    def select(self, *, date: str, count: int = 5) -> tuple[DailyReviewItem, ...]:
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_DAILY_REVIEW_COUNT:
            raise ValueError(f"count must be an integer from 1 to {MAX_DAILY_REVIEW_COUNT}")
        day = _review_date(date)
        candidates = self._candidates(day, date)
        selected: list[_Candidate] = []
        used_topics: set[str] = set()
        priorities = sorted(
            {candidate.item.manual_priority for candidate in candidates},
            key=lambda value: (value is not None, value if value is not None else -1),
            reverse=True,
        )
        for priority in priorities:
            layer = sorted(
                (candidate for candidate in candidates if candidate.item.manual_priority == priority),
                key=lambda candidate: candidate.base_rank,
                reverse=True,
            )
            while layer and len(selected) < count:
                diverse = next(
                    (candidate for candidate in layer
                     if candidate.item.primary_topic != "Uncategorized"
                     and candidate.item.primary_topic.casefold() not in used_topics),
                    None,
                )
                chosen = diverse or layer[0]
                layer.remove(chosen)
                selected.append(chosen)
                if chosen.item.primary_topic != "Uncategorized":
                    used_topics.add(chosen.item.primary_topic.casefold())
            if len(selected) == count:
                break
        return tuple(candidate.item for candidate in selected)

    def _candidates(self, day: Date, date_text: str) -> list[_Candidate]:
        rows = self.repository.connection.execute(
            """SELECT d.*, COALESCE(rs.status, 'unread') AS review_status,
                      rs.manual_priority, rs.priority_reason AS manual_reason,
                      rs.last_reviewed_at
               FROM documents d LEFT JOIN reading_state rs ON rs.document_id=d.id
               WHERE COALESCE(rs.status, 'unread') NOT IN ('read', 'ignored', 'reading', 'archived')
               ORDER BY d.identity_key"""
        ).fetchall()
        cutoff = datetime.combine(day - timedelta(days=30), datetime.min.time(), timezone.utc)
        review_end = datetime.combine(day, datetime.max.time(), timezone.utc)
        candidates: list[_Candidate] = []
        for row in rows:
            reviewed = _timestamp(row["last_reviewed_at"])
            if reviewed is not None and (reviewed > cutoff):
                # This also excludes future timestamps, which cannot be treated as due.
                continue
            stable_id = stable_document_id(row["identity_key"])
            reading, evergreen, reason, topic = self._current_ai(row)
            anchor = reviewed or _timestamp(row["source_created_at"]) or _timestamp(row["created_at"])
            age_days = max(0, (review_end - anchor).days) if anchor else 0
            tie = sha256(f"{date_text}\0{stable_id}".encode("utf-8")).hexdigest()
            item = DailyReviewItem(
                document_id=int(row["id"]), stable_id=stable_id,
                title=row["title"] or "Untitled", source_url=row["canonical_url"] or "",
                status=row["review_status"], manual_priority=row["manual_priority"],
                ai_reading_priority=reading, evergreen_score=evergreen,
                priority_reason=row["manual_reason"] or reason or "Review and decide whether to keep it.",
                primary_topic=topic,
            )
            candidates.append(_Candidate(item, age_days, tie))
        return candidates

    def _current_ai(self, document: sqlite3.Row) -> tuple[int, int, str, str]:
        expected = derivation_input_hash(
            document["source_content_hash"], document["normalized_content_hash"],
            document["normalization_version"],
        )
        rows = self.repository.connection.execute(
            """SELECT payload_json FROM derivations
               WHERE document_id=? AND kind='article' AND status='accepted'
                 AND input_hash=? AND source_content_hash=? AND normalized_content_hash=?
                 AND normalization_version=? AND prompt_version=?
               ORDER BY created_at DESC, id DESC""",
            (document["id"], expected, document["source_content_hash"],
             document["normalized_content_hash"], document["normalization_version"],
             ARTICLE_PROMPT_VERSION),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                derivation = validate_article_derivation(payload, source_text=document["plain_content"] or "")
            except (json.JSONDecodeError, TypeError, DerivationValidationError):
                continue
            topics = sorted(
                derivation.topics,
                key=lambda topic: (-topic.confidence, topic.name.casefold(), topic.name),
            )
            return (
                derivation.reading_priority,
                derivation.evergreen_score,
                derivation.priority_reason,
                topics[0].name if topics else "Uncategorized",
            )
        return 0, 0, "", "Uncategorized"

    def _resolve_stable_id(self, value: str) -> int:
        if not isinstance(value, str) or not re.fullmatch(r"document-[0-9a-f]{32}", value):
            raise ValueError("DOCUMENT_ID must be a stable document ID")
        matches = [
            int(row["id"])
            for row in self.repository.connection.execute("SELECT id, identity_key FROM documents")
            if stable_document_id(row["identity_key"]) == value
        ]
        if len(matches) != 1:
            raise DocumentNotFoundError(f"stable document ID {value} does not exist")
        return matches[0]

    def mark(self, stable_id: str, *, status: str, date: str) -> ReadingState:
        if status not in {"read", "queued", "ignored"}:
            raise ValueError("status must be read, queued, or ignored")
        day = _review_date(date)
        document_id = self._resolve_stable_id(stable_id)
        self.repository.set_reading_state(
            document_id, status=status, last_reviewed=f"{day.isoformat()}T00:00:00Z"
        )
        state = self.repository.get_reading_state(document_id)
        assert state is not None
        return state

    def state(self, stable_id: str) -> ReadingState | None:
        return self.repository.get_reading_state(self._resolve_stable_id(stable_id))


__all__ = [
    "DailyReviewItem", "DailyReviewService", "DocumentNotFoundError",
    "MAX_DAILY_REVIEW_COUNT", "READING_STATUSES", "ReadingState", "normalize_tag",
]
