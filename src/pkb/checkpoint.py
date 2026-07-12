from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Checkpoint:
    offset: int = 0
    exported: int = 0


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Checkpoint:
        if not self.path.exists():
            return Checkpoint()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return Checkpoint(offset=int(payload["offset"]), exported=int(payload["exported"]))

    def save(self, offset: int, exported: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "offset": offset,
            "exported": exported,
            "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
