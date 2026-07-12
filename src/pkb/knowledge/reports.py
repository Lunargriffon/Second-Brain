"""Serializable reports produced by knowledge index builds."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class IndexError:
    path: str
    line: int
    error: str


@dataclass(frozen=True)
class IndexReport:
    discovered: int = 0
    processed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    derivation_jobs_queued: int = 0
    normalization_stale: int = 0
    search_projections_updated: int = 0
    errors: tuple[IndexError, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["errors"] = list(payload["errors"])
        return payload

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
