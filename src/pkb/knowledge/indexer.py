"""Incrementally coordinate raw JSONL ingestion and rebuildable projections."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Mapping
from contextlib import nullcontext

from pkb.sources.base import SourceAdapter
from pkb.sources.x_bookmarks import XBookmarkAdapter
from pkb.sources.zhihu import ZhihuAdapter

from .fingerprint import normalized_content_hash, source_content_hash
from .reports import IndexError, IndexReport
from .repository import KnowledgeRepository
from .search import SearchIndex


class IndexBuildError(RuntimeError):
    """Raised for a malformed raw record when strict indexing is requested."""


class KnowledgeIndexer:
    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        normalization_version: int = 1,
        pipeline_version: str = "article-v1",
    ):
        self.repository = repository
        self.normalization_version = normalization_version
        self.pipeline_version = pipeline_version

    @staticmethod
    def _adapter(path: Path) -> SourceAdapter | None:
        name = path.name.casefold()
        if ".sample" in name or ".backup" in name or name.endswith(".bak.jsonl"):
            return None
        if name.startswith("zhihu") and name.endswith(".jsonl"):
            return ZhihuAdapter()
        if name.startswith("x-bookmarks") and name.endswith(".jsonl"):
            return XBookmarkAdapter()
        return None

    def build(self, raw_dir: str | Path, *, strict: bool = False) -> IndexReport:
        root = Path(raw_dir)
        if strict:
            self._validate_all(root)
        report = IndexReport()
        search = SearchIndex(self.repository)
        transaction = self.repository.connection if strict else nullcontext()
        with transaction:
            report = self._build(root, report, search, strict=strict)
        return report

    def _paths(self, root: Path) -> list[Path]:
        root_resolved = root.resolve()
        paths = []
        for path in sorted(root.glob("*.jsonl")):
            name = path.name.casefold()
            if path.is_symlink() and not path.resolve().is_relative_to(root_resolved):
                continue
            if any(marker in name for marker in (".tmp", ".temp")):
                continue
            paths.append(path)
        return paths

    def _validate_all(self, root: Path) -> None:
        for path in self._paths(root):
            adapter = self._adapter(path)
            if adapter is None:
                continue
            with path.open("r", encoding="utf-8-sig") as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                        if not isinstance(value, Mapping):
                            raise ValueError("record")
                        adapter.normalize(value, raw_path=path, raw_line=line_number)
                    except (json.JSONDecodeError, TypeError, ValueError) as exc:
                        code = self._error_code(exc)
                        raise IndexBuildError(f"{path.name}:{line_number}: {code}") from exc

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, json.JSONDecodeError):
            return "invalid_json"
        if isinstance(exc, TypeError):
            return "invalid_type"
        return "invalid_record"

    def _build(
        self, root: Path, report: IndexReport, search: SearchIndex, *, strict: bool
    ) -> IndexReport:
        for path in self._paths(root):
            report = replace(report, discovered=report.discovered + 1)
            adapter = self._adapter(path)
            if adapter is None:
                report = replace(report, skipped=report.skipped + 1)
                continue
            with path.open("r", encoding="utf-8-sig") as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                        if not isinstance(value, Mapping):
                            raise ValueError("JSON record must be an object")
                        document = adapter.normalize(value, raw_path=path, raw_line=line_number)
                        source_hash = source_content_hash(value)
                        normalized_hash = normalized_content_hash(
                            document.title, document.author, document.plain_content,
                            document.canonical_url, document.media_urls,
                        )
                        outcome = self.repository.upsert_document(
                            document,
                            source_hash=source_hash,
                            normalized_hash=normalized_hash,
                            normalization_version=self.normalization_version,
                            commit=not strict,
                        )
                        report = replace(report, processed=report.processed + 1)
                        if outcome.created:
                            report = replace(report, created=report.created + 1)
                        elif outcome.source_changed or outcome.normalized_changed:
                            report = replace(report, updated=report.updated + 1)
                        else:
                            report = replace(report, unchanged=report.unchanged + 1)

                        if outcome.created or outcome.source_changed:
                            if self.repository.enqueue_derivation_job(
                                outcome.document_id,
                                input_hash=source_hash,
                                pipeline_version=self.pipeline_version,
                                commit=not strict,
                            ):
                                report = replace(
                                    report,
                                    derivation_jobs_queued=report.derivation_jobs_queued + 1,
                                )
                        elif outcome.normalized_changed:
                            report = replace(
                                report, normalization_stale=report.normalization_stale + 1
                            )
                        if search.ensure_current(outcome.document_id, commit=not strict):
                            report = replace(
                                report,
                                search_projections_updated=report.search_projections_updated + 1,
                            )
                    except (json.JSONDecodeError, TypeError, ValueError) as exc:
                        code = self._error_code(exc)
                        error = IndexError(path.name, line_number, code)
                        if strict:
                            raise IndexBuildError(f"{path.name}:{line_number}: {error.error}") from exc
                        report = replace(
                            report,
                            failed=report.failed + 1,
                            errors=(*report.errors, error),
                        )
        return report
