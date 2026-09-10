"""Atomic, resumable persistence for Douyin favorite manifests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .eligibility import Eligibility, EligibilityDecision
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
        if payload.get("version") not in {1, 2}:
            raise ValueError("unsupported manifest version")
        return [FavoriteItem.from_dict(value) for value in payload.get("items", [])]

    def get(self, work_id: str) -> FavoriteItem:
        for entry in self.items():
            if entry.work_id == work_id:
                return entry
        raise KeyError(work_id)

    def pending(self) -> list[FavoriteItem]:
        return [
            entry
            for entry in self.items()
            if entry.stage not in _TERMINAL_STAGES
            and entry.eligibility is not Eligibility.EXCLUDE
        ]

    def discover(self, discovered: Iterable[FavoriteItem]) -> None:
        entries = self.items()
        known = {entry.work_id: index for index, entry in enumerate(entries)}
        for entry in discovered:
            if entry.work_id not in known:
                known[entry.work_id] = len(entries)
                entries.append(entry)
                continue
            index = known[entry.work_id]
            existing = entries[index]
            entries[index] = replace(
                existing,
                url=entry.url,
                author_id=entry.author_id,
                author=entry.author,
                caption=entry.caption,
                hashtags=entry.hashtags,
                published_at=entry.published_at,
                observed_at=entry.observed_at,
            )
        self._save(entries)

    def set_eligibility(
        self, work_id: str, decision: EligibilityDecision
    ) -> FavoriteItem:
        entries = self.items()
        for index, entry in enumerate(entries):
            if entry.work_id == work_id:
                updated = entry.with_eligibility(decision)
                entries[index] = updated
                self._save(entries)
                return updated
        raise KeyError(work_id)

    def needs_classification(
        self, work_id: str, classifier_version: str, input_hash: str
    ) -> bool:
        entry = self.get(work_id)
        return (
            entry.eligibility is None
            or entry.classifier_version != classifier_version
            or entry.classification_input_hash != input_hash
        )

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
        payload = {"version": 2, "items": [entry.to_dict() for entry in entries]}
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.path)
