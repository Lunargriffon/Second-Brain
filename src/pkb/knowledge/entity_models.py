"""Immutable values used by the Entity knowledge repository."""

from __future__ import annotations

from dataclasses import dataclass


class InvalidEntityTransition(ValueError):
    """Raised when an Entity review-state transition is not permitted."""


class EntityMergeCycleError(ValueError):
    """Raised when a canonical Entity chain contains a cycle."""


class EntityNotFoundError(LookupError):
    """Raised when an Entity does not exist."""


@dataclass(frozen=True)
class Entity:
    id: int
    entity_type: str
    canonical_name: str
    normalized_name: str
    review_status: str
    merged_into_entity_id: int | None


@dataclass(frozen=True)
class Alias:
    id: int
    entity_id: int
    alias_kind: str
    alias_value: str
    normalized_value: str
    namespace: str
    is_strong_identifier: bool
    review_status: str


@dataclass(frozen=True)
class Mention:
    id: int
    document_id: int
    entity_id: int | None
    surface_text: str
    start_offset: int
    end_offset: int
    review_status: str
