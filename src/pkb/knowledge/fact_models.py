"""Immutable typed values and stable identity keys for grounded facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import re
import unicodedata


FACT_KEY_VERSION = 1
_WHITESPACE = re.compile(r"\s+")
_SCALAR_TYPES = {"string", "number", "boolean", "date", "datetime", "money", "quantity"}


@dataclass(frozen=True)
class EntityObject:
    entity_id: int

    def __post_init__(self) -> None:
        if isinstance(self.entity_id, bool) or not isinstance(self.entity_id, int) or self.entity_id <= 0:
            raise ValueError("entity_id must be a positive integer")


@dataclass(frozen=True)
class ScalarObject:
    object_type: str
    value: str | int | float | bool | dict[str, object]

    def __post_init__(self) -> None:
        if self.object_type not in _SCALAR_TYPES:
            raise ValueError(f"unsupported scalar object type: {self.object_type}")
        if self.object_type == "boolean" and not isinstance(self.value, bool):
            raise ValueError("boolean facts require a bool value")
        if self.object_type == "number" and (
            isinstance(self.value, bool) or not isinstance(self.value, (int, float))
        ):
            raise ValueError("number facts require an int or float value")
        if self.object_type in {"money", "quantity"} and not isinstance(self.value, dict):
            raise ValueError(f"{self.object_type} facts require an object value")
        if self.object_type in {"string", "date", "datetime"} and not isinstance(self.value, str):
            raise ValueError(f"{self.object_type} facts require a string value")


@dataclass(frozen=True)
class Fact:
    id: int
    fact_key: str
    subject_entity_id: int
    predicate: str
    object_entity_id: int | None
    object_value: object | None
    object_type: str
    valid_from: str | None
    valid_to: str | None
    observed_at: str | None
    confidence: float | None
    review_status: str
    knowledge_status: str | None


@dataclass(frozen=True)
class Evidence:
    id: int
    fact_id: int
    document_text_version_id: int
    document_id: int
    role: str
    excerpt: str
    start_offset: int
    end_offset: int
    observed_at: str | None
    validation_status: str


@dataclass(frozen=True)
class FactRelation:
    id: int
    left_fact_id: int
    right_fact_id: int
    relation_type: str
    confidence: float | None
    explanation: str
    deterministic_validation_status: str
    review_status: str
    knowledge_status: str | None


def normalize_predicate(predicate: str) -> str:
    normalized = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", predicate).strip()).casefold()
    if not normalized:
        raise ValueError("predicate must not be empty")
    return normalized


def _iso(value: str | None) -> str:
    if value is None:
        return ""
    candidate = value.strip()
    try:
        if "T" in candidate or " " in candidate:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).isoformat()
        return date.fromisoformat(candidate).isoformat()
    except ValueError as error:
        raise ValueError(f"invalid ISO time: {value}") from error


def _canonical_scalar(value: ScalarObject) -> object:
    if value.object_type == "string":
        return unicodedata.normalize("NFKC", str(value.value))
    if value.object_type in {"date", "datetime"}:
        return _iso(str(value.value))
    return value.value


def canonical_object(value: EntityObject | ScalarObject) -> tuple[str, object, str]:
    """Return object type, JSON value, and searchable normalized text."""
    if isinstance(value, EntityObject):
        return "entity", value.entity_id, str(value.entity_id)
    if not isinstance(value, ScalarObject):
        raise TypeError("fact object must be EntityObject or ScalarObject")
    canonical = _canonical_scalar(value)
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value.object_type, canonical, encoded


def fact_key(
    subject_entity_id: int,
    predicate: str,
    object_value: EntityObject | ScalarObject,
    valid_from: str | None,
    valid_to: str | None,
) -> str:
    """Return the versioned SHA-256 identity of a canonical Fact tuple."""
    object_type, canonical, _ = canonical_object(object_value)
    payload = {
        "fact_key_version": FACT_KEY_VERSION,
        "object": canonical,
        "object_type": object_type,
        "predicate": normalize_predicate(predicate),
        "subject_entity_id": subject_entity_id,
        "valid_from": _iso(valid_from),
        "valid_to": _iso(valid_to),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "FACT_KEY_VERSION", "EntityObject", "ScalarObject", "Fact", "Evidence", "FactRelation",
    "canonical_object", "fact_key", "normalize_predicate",
]
