"""Canonical cross-domain job scopes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

SCOPE_TYPES = frozenset({"document", "entity", "fact", "fact_relation", "synthesis"})
SCOPE_ROLES = frozenset({"anchor", "input", "output", "context"})


@dataclass(frozen=True, slots=True)
class JobScope:
    scope_type: str
    scope_id: str
    scope_role: str
    ordinal: int = 0

    def __post_init__(self) -> None:
        if self.scope_type not in SCOPE_TYPES:
            raise ValueError(f"invalid scope type: {self.scope_type!r}")
        if self.scope_role not in SCOPE_ROLES:
            raise ValueError(f"invalid scope role: {self.scope_role!r}")
        if not self.scope_id or self.scope_id != self.scope_id.strip():
            raise ValueError("scope_id must be non-empty and already trimmed")
        if self.ordinal < 0:
            raise ValueError("scope ordinal must be non-negative")


def canonical_scopes(scopes: Iterable[JobScope]) -> tuple[JobScope, ...]:
    """Validate and return scopes in their stable identity order."""
    values = tuple(scopes)
    if sum(scope.scope_role == "anchor" for scope in values) != 1:
        raise ValueError("scope set requires exactly one anchor")
    positions = [(scope.scope_role, scope.ordinal) for scope in values]
    if len(positions) != len(set(positions)):
        raise ValueError("duplicate scope role/ordinal")
    return tuple(
        sorted(
            values,
            key=lambda scope: (
                scope.scope_role,
                scope.ordinal,
                scope.scope_type,
                scope.scope_id,
            ),
        )
    )


def canonical_scope_json(scopes: Iterable[JobScope]) -> str:
    """Encode a validated scope set as compact deterministic JSON."""
    payload = [
        {
            "ordinal": scope.ordinal,
            "role": scope.scope_role,
            "type": scope.scope_type,
            "id": scope.scope_id,
        }
        for scope in canonical_scopes(scopes)
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def scope_hash(scopes: Iterable[JobScope]) -> str:
    """Return the SHA-256 identity for a canonical scope set."""
    return hashlib.sha256(canonical_scope_json(scopes).encode("utf-8")).hexdigest()
