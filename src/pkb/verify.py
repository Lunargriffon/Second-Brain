from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VerifyResult:
    collection_id: str
    scanned_total: int
    exported_total: int
    status: str


def verify_zhihu_exports(audit_path: Path, raw_dir: Path) -> list[VerifyResult]:
    results: list[VerifyResult] = []
    for line in audit_path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        collection_id = str(record["collection_id"])
        scanned_total = int(record["scanned_total"])
        raw_path = raw_dir / f"zhihu-{collection_id}.jsonl"
        exported_total = _count_lines(raw_path)
        status = "ok" if scanned_total == exported_total else "mismatch"
        results.append(
            VerifyResult(
                collection_id=collection_id,
                scanned_total=scanned_total,
                exported_total=exported_total,
                status=status,
            )
        )
    return results


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
