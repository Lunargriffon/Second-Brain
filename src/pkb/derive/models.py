"""Strict value objects and validation for article derivations."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Mapping


CONTENT_TYPES = frozenset({"tutorial", "argument", "reference", "story", "news", "other"})
_ARTICLE_FIELDS = frozenset(
    {
        "summary",
        "key_points",
        "topics",
        "tags",
        "content_type",
        "evergreen_score",
        "reading_priority",
        "priority_reason",
        "source_citations",
    }
)


class DerivationValidationError(ValueError):
    """Raised when a structured derivation violates its schema."""


@dataclass(frozen=True)
class ScoredLabel:
    name: str
    confidence: float


@dataclass(frozen=True)
class SourceCitation:
    claim: str
    excerpt: str


@dataclass(frozen=True)
class ArticleDerivation:
    summary: str
    key_points: tuple[str, ...]
    topics: tuple[ScoredLabel, ...]
    tags: tuple[ScoredLabel, ...]
    content_type: str
    evergreen_score: int
    reading_priority: int
    priority_reason: str
    source_citations: tuple[SourceCitation, ...]


def _error(field: str, message: str) -> DerivationValidationError:
    return DerivationValidationError(f"{field}: {message}")


def _strict_object(value: object, *, field: str, keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _error(field, "must be an object")
    unknown = set(value) - keys
    if unknown:
        raise _error(field, f"unknown fields: {', '.join(sorted(map(str, unknown)))}")
    missing = keys - set(value)
    if missing:
        raise _error(field, f"missing fields: {', '.join(sorted(missing))}")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(field, "must be a non-empty string")
    return value


def _list(value: object, *, field: str, limit: int) -> list[object]:
    if not isinstance(value, list):
        raise _error(field, "must be a list")
    if len(value) > limit:
        raise _error(field, f"must contain at most {limit} items")
    return value


def _score(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(field, "must be an integer")
    if not 0 <= value <= 5:
        raise _error(field, "must be between 0 and 5")
    return value


def _labels(value: object, *, field: str, limit: int) -> tuple[ScoredLabel, ...]:
    labels: list[ScoredLabel] = []
    for index, item in enumerate(_list(value, field=field, limit=limit)):
        item_field = f"{field}[{index}]"
        obj = _strict_object(item, field=item_field, keys=frozenset({"name", "confidence"}))
        confidence = obj["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, Real):
            raise _error(f"{item_field}.confidence", "must be a number")
        if not 0 <= confidence <= 1:
            raise _error(f"{item_field}.confidence", "must be between 0 and 1")
        labels.append(
            ScoredLabel(
                name=_text(obj["name"], field=f"{item_field}.name"),
                confidence=float(confidence),
            )
        )
    return tuple(labels)


def _normalize_whitespace(value: str) -> str:
    return "".join(value.split())


def validate_article_derivation(
    payload: Mapping[str, object], *, source_text: str
) -> ArticleDerivation:
    """Validate and convert an untrusted structured article derivation."""

    obj = _strict_object(payload, field="derivation", keys=_ARTICLE_FIELDS)
    content_type = _text(obj["content_type"], field="content_type")
    if content_type not in CONTENT_TYPES:
        raise _error("content_type", f"must be one of {', '.join(sorted(CONTENT_TYPES))}")

    key_points = tuple(
        _text(item, field=f"key_points[{index}]")
        for index, item in enumerate(_list(obj["key_points"], field="key_points", limit=10))
    )
    citations: list[SourceCitation] = []
    normalized_source = _normalize_whitespace(source_text)
    citation_items = _list(obj["source_citations"], field="source_citations", limit=100)
    if not citation_items:
        raise _error("source_citations", "must contain at least one citation")
    for index, item in enumerate(citation_items):
        item_field = f"source_citations[{index}]"
        citation = _strict_object(item, field=item_field, keys=frozenset({"claim", "excerpt"}))
        claim = _text(citation["claim"], field=f"{item_field}.claim")
        excerpt = _text(citation["excerpt"], field=f"{item_field}.excerpt")
        if _normalize_whitespace(excerpt) not in normalized_source:
            raise _error(f"{item_field}.excerpt", "was not found in source text")
        citations.append(SourceCitation(claim=claim, excerpt=excerpt))

    return ArticleDerivation(
        summary=_text(obj["summary"], field="summary"),
        key_points=key_points,
        topics=_labels(obj["topics"], field="topics", limit=5),
        tags=_labels(obj["tags"], field="tags", limit=8),
        content_type=content_type,
        evergreen_score=_score(obj["evergreen_score"], field="evergreen_score"),
        reading_priority=_score(obj["reading_priority"], field="reading_priority"),
        priority_reason=_text(obj["priority_reason"], field="priority_reason"),
        source_citations=tuple(citations),
    )
