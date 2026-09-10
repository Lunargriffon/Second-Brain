"""Atomic filtering of an existing Douyin knowledge corpus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Mapping

from .eligibility import Eligibility
from .manifest import ManifestStore


@dataclass(frozen=True)
class CorpusRebuildReport:
    kept: int
    removed: int
    changed: bool
    backup_path: Path | None


def _unique_backup_path(backup_root: Path, raw_path: Path, now: datetime) -> Path:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = backup_root / f"{raw_path.stem}.{stamp}.bak.jsonl"
    suffix = 1
    while candidate.exists():
        candidate = backup_root / f"{raw_path.stem}.{stamp}.{suffix}.bak.jsonl"
        suffix += 1
    return candidate


def rebuild_filtered_corpus(
    raw_path: Path,
    manifest: ManifestStore,
    backup_root: Path,
    *,
    now: datetime | None = None,
) -> CorpusRebuildReport:
    """Remove non-knowledge records atomically after validating the full input."""

    raw_path = Path(raw_path)
    if not raw_path.exists():
        return CorpusRebuildReport(0, 0, False, None)
    entries = manifest.items()
    if any(entry.eligibility is None for entry in entries):
        raise ValueError("manifest contains unclassified items")
    decisions = {entry.work_id: entry.eligibility for entry in entries}
    raw_lines = raw_path.read_bytes().splitlines(keepends=True)
    parsed: list[tuple[str, bytes, Mapping[str, object]]] = []
    seen: set[str] = set()
    for index, raw_line in enumerate(raw_lines, 1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line.decode("utf-8-sig" if index == 1 else "utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid raw JSONL") from exc
        if not isinstance(value, Mapping) or not str(value.get("work_id", "")):
            raise ValueError("raw record is missing work ID")
        work_id = str(value["work_id"])
        if work_id in seen:
            raise ValueError(f"duplicate work ID: {work_id}")
        seen.add(work_id)
        if work_id not in decisions:
            raise ValueError("raw record is absent from manifest")
        parsed.append((work_id, raw_line, value))

    for _work_id, _raw_line, value in parsed:
        if not str(value.get("transcript_text", "")).strip():
            raise ValueError("raw record has empty transcript")

    kept_lines = [
        raw_line
        for work_id, raw_line, _value in parsed
        if decisions[work_id] is Eligibility.KEEP
    ]
    removed = len(parsed) - len(kept_lines)
    if removed == 0:
        return CorpusRebuildReport(len(kept_lines), 0, False, None)

    backup_root = Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = _unique_backup_path(
        backup_root, raw_path, now or datetime.now(timezone.utc)
    )
    shutil.copy2(raw_path, backup_path)

    temporary = raw_path.with_suffix(raw_path.suffix + ".filtering.tmp")
    try:
        with temporary.open("wb") as stream:
            for raw_line in kept_lines:
                stream.write(raw_line.rstrip(b"\r\n") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(raw_path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return CorpusRebuildReport(len(kept_lines), removed, True, backup_path)
