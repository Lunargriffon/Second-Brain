"""Atomic, resumable persistence for Douyin favorite manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import FavoriteItem, Stage


_TERMINAL_STAGES = {Stage.CLEANED, Stage.UNAVAILABLE}


class ManifestStore:
    """Persist favorite processing state without partially replacing checkpoints."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def items(self) -> list[FavoriteItem]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise ValueError("unsupported manifest version")
        return [FavoriteItem.from_dict(value) for value in payload.get("items", [])]

    def get(self, work_id: str) -> FavoriteItem:
        for entry in self.items():
            if entry.work_id == work_id:
                return entry
        raise KeyError(work_id)

    def pending(self) -> list[FavoriteItem]:
        return [entry for entry in self.items() if entry.stage not in _TERMINAL_STAGES]

    def discover(self, discovered: Iterable[FavoriteItem]) -> None:
        entries = self.items()
        known = {entry.work_id for entry in entries}
        for entry in discovered:
            if entry.work_id not in known:
                entries.append(entry)
                known.add(entry.work_id)
        self._save(entries)

    def update(self, work_id: str, stage: Stage) -> FavoriteItem:
        entries = self.items()
        for index, entry in enumerate(entries):
            if entry.work_id == work_id:
                updated = entry.transition(stage)
                entries[index] = updated
                self._save(entries)
                return updated
        raise KeyError(work_id)

    def _save(self, entries: Iterable[FavoriteItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": 1, "items": [entry.to_dict() for entry in entries]}
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.path)
