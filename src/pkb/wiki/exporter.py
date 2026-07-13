"""Safe, deterministic export of generated Markdown into an Obsidian vault."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from collections.abc import Iterable


MANIFEST = ".pkb-generated.json"
_GENERATED_MARKER = re.compile(r'^generated_by:\s*["\']?pkb["\']?\s*$', re.MULTILINE)
_SOURCE_ID = re.compile(r'^id:\s*["\']?([^"\'\s]+)["\']?\s*$', re.MULTILINE)
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]*)?\]\]")


@dataclass(frozen=True)
class GeneratedFile:
    path: str
    content: str
    document_id: str


@dataclass(frozen=True)
class ExportResult:
    written: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckReport:
    broken_links: tuple[str, ...] = ()
    creatable_user_notes: tuple[str, ...] = ()
    missing_source_ids: tuple[str, ...] = ()
    manifest_mismatches: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    unmarked_collisions: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(
            (
                self.broken_links,
                self.missing_source_ids,
                self.manifest_mismatches,
                self.modified,
                self.unmarked_collisions,
                self.stale,
            )
        )


def _safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"unsafe generated path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(
        part in {"", ".", ".."} or ":" in part for part in path.parts
    ):
        raise ValueError(f"unsafe generated path: {value!r}")
    normalized = path.as_posix()
    if normalized == MANIFEST:
        raise ValueError(f"reserved generated path: {value!r}")
    if path.parts[0].casefold() == "user":
        raise ValueError("generated files may not be written under vault/user")
    return normalized


def _fingerprint(data: bytes) -> str:
    return sha256(data).hexdigest()


def _target(vault: Path, relative: str, *, allow_user: bool = False) -> Path:
    path = PurePosixPath(relative)
    if allow_user and path.parts and path.parts[0].casefold() == "user":
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"unsafe generated path: {relative!r}")
    else:
        _safe_path(relative)
    root = vault.resolve()
    candidate = vault.joinpath(*path.parts)
    current = vault
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"path contains symlink: {relative!r}")
    parent = candidate.parent
    # Resolving the parent catches an existing symlink before any write occurs.
    resolved_parent = parent.resolve()
    if not resolved_parent.is_relative_to(root):
        raise ValueError(f"path escapes vault through symlink: {relative!r}")
    return candidate


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class VaultExporter:
    def __init__(self, files: Iterable[GeneratedFile], *, renderer_version: str):
        if not renderer_version:
            raise ValueError("renderer_version must be non-empty")
        desired: dict[str, GeneratedFile] = {}
        for item in files:
            path = _safe_path(item.path)
            if path in desired:
                raise ValueError(f"duplicate generated path: {path}")
            if not isinstance(item.content, str) or not isinstance(item.document_id, str) or not item.document_id:
                raise ValueError("generated content and document_id must be strings")
            if not _GENERATED_MARKER.search(item.content):
                raise ValueError(f"generated marker missing from {path}")
            desired[path] = GeneratedFile(path, item.content, item.document_id)
        self._files = desired
        self.renderer_version = renderer_version

    def _read_manifest(self, vault: Path) -> dict[str, dict[str, str]]:
        path = vault / MANIFEST
        if not path.exists():
            return {}
        if path.is_symlink():
            raise ValueError("generated manifest may not be a symlink")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
            raise ValueError("invalid generated manifest")
        result: dict[str, dict[str, str]] = {}
        for raw in payload["files"]:
            if not isinstance(raw, dict):
                raise ValueError("invalid generated manifest entry")
            relative = _safe_path(raw.get("path"))
            required = ("fingerprint", "renderer_version", "document_id")
            if any(not isinstance(raw.get(key), str) or not raw[key] for key in required):
                raise ValueError("invalid generated manifest entry")
            if relative in result:
                raise ValueError("duplicate generated manifest path")
            result[relative] = {key: raw[key] for key in ("path", *required)}
        return result

    def _manifest_bytes(self, entries: dict[str, dict[str, str]]) -> bytes:
        payload = {
            "renderer_version": self.renderer_version,
            "files": [entries[path] for path in sorted(entries)],
        }
        return (json.dumps(payload, ensure_ascii=False, sort_keys=False, indent=2) + "\n").encode("utf-8")

    def export(self, vault: Path, *, remove_stale: bool = False) -> ExportResult:
        """Export as a recoverable batch, rolling page changes back on failure."""

        vault = Path(vault)
        vault.mkdir(parents=True, exist_ok=True)
        previous = self._read_manifest(vault)
        affected = set(self._files)
        if remove_stale:
            affected.update(set(previous) - set(self._files))
        backups: dict[Path, bytes | None] = {}
        for relative in sorted(affected):
            target = _target(vault, relative)
            backups[target] = target.read_bytes() if target.exists() else None
        try:
            return self._export_once(vault, remove_stale=remove_stale)
        except BaseException:
            for target, original in backups.items():
                try:
                    if original is None:
                        if target.exists() and not target.is_symlink():
                            target.unlink()
                    elif not target.exists() or target.read_bytes() != original:
                        _atomic_write(target, original)
                except OSError:
                    # Preserve the operation's original exception. A subsequent
                    # check will still surface any filesystem recovery failure.
                    pass
            raise

    def _export_once(self, vault: Path, *, remove_stale: bool) -> ExportResult:
        vault = Path(vault)
        previous = self._read_manifest(vault)
        written: list[str] = []
        unchanged: list[str] = []
        conflicts: list[str] = []
        removed: list[str] = []
        next_entries: dict[str, dict[str, str]] = {}

        for relative in sorted(self._files):
            item = self._files[relative]
            target = _target(vault, relative)
            data = item.content.encode("utf-8")
            digest = _fingerprint(data)
            old = previous.get(relative)
            if target.exists():
                current = _fingerprint(target.read_bytes())
                if (
                    old is None
                    or current != old["fingerprint"]
                    or old["document_id"] != item.document_id
                    or old["renderer_version"] != self.renderer_version
                ):
                    conflicts.append(relative)
                    if old is not None:
                        next_entries[relative] = old
                    continue
                if current == digest:
                    unchanged.append(relative)
                else:
                    _atomic_write(target, data)
                    written.append(relative)
            else:
                # A missing manifest-owned file is safe to recreate.
                _atomic_write(target, data)
                written.append(relative)
            next_entries[relative] = {
                "path": relative,
                "fingerprint": digest,
                "renderer_version": self.renderer_version,
                "document_id": item.document_id,
            }

        stale = sorted(set(previous) - set(self._files))
        for relative in stale:
            target = _target(vault, relative)
            old = previous[relative]
            if remove_stale and target.exists() and _fingerprint(target.read_bytes()) == old["fingerprint"]:
                target.unlink()
                removed.append(relative)
            else:
                next_entries[relative] = old

        # Never create an ownership manifest when every desired path collided.
        if next_entries or (vault / MANIFEST).exists():
            manifest_path = vault / MANIFEST
            manifest_data = self._manifest_bytes(next_entries)
            if not manifest_path.exists() or manifest_path.read_bytes() != manifest_data:
                _atomic_write(manifest_path, manifest_data)
        return ExportResult(
            tuple(written), tuple(unchanged), tuple(conflicts), tuple(stale), tuple(removed)
        )

    def check(self, vault: Path) -> CheckReport:
        vault = Path(vault)
        manifest = self._read_manifest(vault)
        modified: set[str] = set()
        missing_ids: set[str] = set()
        mismatches: set[str] = set()
        unmarked: set[str] = set()
        broken: set[str] = set()
        creatable: set[str] = set()

        for relative, old in manifest.items():
            target = _target(vault, relative)
            if not target.exists() or _fingerprint(target.read_bytes()) != old["fingerprint"]:
                modified.add(relative)
            if target.exists() and target.suffix.casefold() == ".md":
                text = target.read_text(encoding="utf-8")
                if PurePosixPath(relative).parts[0] == "articles":
                    sections = text.split("---", 2)
                    frontmatter = sections[1] if len(sections) == 3 else ""
                    source_ids = _SOURCE_ID.findall(frontmatter)
                    if not source_ids:
                        missing_ids.add(relative)
                    elif len(source_ids) != 1 or source_ids[0] != old["document_id"]:
                        mismatches.add(relative)

        for relative, item in self._files.items():
            target = _target(vault, relative)
            old = manifest.get(relative)
            desired_fingerprint = _fingerprint(item.content.encode("utf-8"))
            if (
                old is None
                or old["document_id"] != item.document_id
                or old["renderer_version"] != self.renderer_version
                or old["fingerprint"] != desired_fingerprint
            ):
                mismatches.add(relative)
            if target.exists() and not _GENERATED_MARKER.search(target.read_text(encoding="utf-8")):
                unmarked.add(relative)

        for relative in manifest:
            target = _target(vault, relative)
            if not target.exists() or target.suffix.casefold() != ".md":
                continue
            text = target.read_text(encoding="utf-8")
            source_dir = PurePosixPath(relative).parent
            for raw_link in _WIKILINK.findall(text):
                link = PurePosixPath(raw_link.strip())
                combined = source_dir.joinpath(link)
                parts: list[str] = []
                for part in combined.parts:
                    if part == "..":
                        if not parts:
                            raise ValueError(f"unsafe wikilink in {relative}")
                        parts.pop()
                    elif part not in {"", "."}:
                        parts.append(part)
                if not parts:
                    continue
                resolved = PurePosixPath(*parts)
                if not resolved.suffix:
                    resolved = resolved.with_suffix(".md")
                linked = resolved.as_posix()
                linked_target = _target(vault, linked, allow_user=True)
                if linked_target.exists():
                    continue
                if (
                    resolved.parts[0].casefold() == "user"
                    and len(resolved.parts) == 2
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.md", resolved.parts[1])
                ):
                    creatable.add(linked)
                else:
                    broken.add(linked)

        stale = set(manifest) - set(self._files)
        return CheckReport(
            tuple(sorted(broken)),
            tuple(sorted(creatable)),
            tuple(sorted(missing_ids)),
            tuple(sorted(mismatches)),
            tuple(sorted(modified)),
            tuple(sorted(unmarked)),
            tuple(sorted(stale)),
        )
