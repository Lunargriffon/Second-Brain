"""Offline, evidence-preserving acceptance orchestration for Second Brain."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from contextlib import closing
from pathlib import Path
from typing import Iterable, Sequence

from pkb.derive.pipeline import DerivationPipeline
from pkb.derive.provider import FakeDerivationProvider
from pkb.knowledge.indexer import KnowledgeIndexer
from pkb.knowledge.repository import KnowledgeRepository
from pkb.knowledge.search import SearchIndex
from pkb.wiki.exporter import MANIFEST, VaultExporter
from pkb.wiki.projection import RENDERER_VERSION, build_generated_files


SEARCH_BENCHMARK_REFERENCE = "docs/knowledge-search-benchmark.md"


@dataclass(frozen=True)
class AcceptanceResult:
    """Aggregate acceptance results safe to write outside the temporary work area."""

    status: str
    logical_hash: str
    raw_hash_before: str
    raw_hash_after: str
    frozen_hash_before: str
    frozen_hash_after: str
    document_count: int
    membership_count: int
    derivation_count: int
    generated_file_count: int
    search_checks_run: int
    search_checks_passed: int
    search_benchmark_reference: str
    vault_check_ok: bool
    second_pass_zero_work: bool
    failures: tuple[str, ...] = ()
    skips: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _evidence_files(root: Path | None) -> dict[str, str]:
    if root is None:
        return {}
    if not root.is_dir():
        raise ValueError("evidence directory does not exist")
    files: dict[str, str] = {}
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names[:] = sorted(directory_names)
        for name in sorted(file_names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                payload = ("symlink:" + os.readlink(path)).encode("utf-8")
                files[relative] = hashlib.sha256(payload).hexdigest()
                continue
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            files[relative] = digest.hexdigest()
    return files


def _map_hash(values: dict[str, str]) -> str:
    encoded = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fake_response(source_text: str) -> dict[str, object]:
    excerpt = source_text.strip()[:120].strip()
    if not excerpt:
        raise ValueError("document has no source text for grounded acceptance derivation")
    return {
        "summary": "Offline acceptance derivation.",
        "key_points": ["Deterministic acceptance check."],
        "topics": [{"name": "acceptance", "confidence": 1.0}],
        "tags": [{"name": "acceptance", "confidence": 1.0}],
        "content_type": "other",
        "evergreen_score": 0,
        "reading_priority": 0,
        "priority_reason": "Offline acceptance only.",
        "source_citations": [{"claim": "Source text is available.", "excerpt": excerpt}],
    }


def _pending_responses(database: Path) -> tuple[list[dict[str, object]], int]:
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            """SELECT j.id, d.plain_content FROM jobs AS j
               JOIN documents AS d ON d.id=j.document_id
               WHERE j.job_type='article' AND j.status='pending'
               ORDER BY j.created_at, j.id"""
        ).fetchall()
        empty_job_ids = [int(row[0]) for row in rows if not (row[1] or "").strip()]
        if empty_job_ids:
            placeholders = ",".join("?" for _ in empty_job_ids)
            connection.execute(
                f"UPDATE jobs SET status='failed', error='empty_source_text' "
                f"WHERE id IN ({placeholders})",
                empty_job_ids,
            )
            connection.commit()
    responses = [_fake_response(row[1]) for row in rows if (row[1] or "").strip()]
    return responses, len(empty_job_ids)


def _rows(connection: sqlite3.Connection, query: str) -> list[dict[str, object]]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(query).fetchall()]


def _logical_hash(database: Path, vault: Path) -> str:
    with closing(sqlite3.connect(database)) as connection:
        documents = _rows(
            connection,
            """SELECT identity_key, source_type, title, author, plain_content,
                      canonical_url, source_created_at, source_content_hash,
                      normalized_content_hash, normalization_version, schema_version
               FROM documents ORDER BY identity_key""",
        )
        memberships = _rows(
            connection,
            """SELECT d.identity_key, m.source, m.source_item_id, m.collection_id,
                      m.collection_title, m.source_url, m.observed_at, m.raw_line
               FROM source_memberships AS m
               JOIN documents AS d ON d.id=m.document_id
               ORDER BY d.identity_key, m.source, m.collection_id, m.source_item_id""",
        )
        derivations = _rows(
            connection,
            """SELECT d.identity_key, x.kind, x.payload_json, x.input_hash,
                      x.source_content_hash, x.normalized_content_hash,
                      x.normalization_version, x.schema_version, x.prompt_version,
                      x.provider, x.model, x.generation_parameters_json, x.status
               FROM derivations AS x JOIN documents AS d ON d.id=x.document_id
               ORDER BY d.identity_key, x.kind, x.input_hash, x.prompt_version,
                        x.payload_json""",
        )
    manifest_path = vault / MANIFEST
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"files": []}
    )
    manifest_entries = sorted(
        manifest.get("files", []), key=lambda item: (item["path"], item["document_id"])
    )
    logical = {
        "documents": documents,
        "memberships": memberships,
        "derivations": derivations,
        "manifest": manifest_entries,
    }
    encoded = json.dumps(logical, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _empty_work_directory(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("acceptance work directory must be empty")
    path.mkdir(parents=True, exist_ok=True)


def run_acceptance(
    raw_dir: str | Path,
    work_dir: str | Path,
    *,
    frozen_dir: str | Path | None = None,
    accepted_queries: Sequence[str] = (),
) -> AcceptanceResult:
    """Run a fresh, bounded acceptance without modifying raw or frozen evidence."""

    raw = Path(raw_dir)
    frozen = Path(frozen_dir) if frozen_dir is not None else None
    work = Path(work_dir)
    _empty_work_directory(work)

    raw_before_map = _evidence_files(raw)
    frozen_before_map = _evidence_files(frozen)
    raw_before = _map_hash(raw_before_map)
    frozen_before = _map_hash(frozen_before_map)
    failures: list[str] = []
    skips: list[str] = []

    database = work / "knowledge.db"
    vault = work / "vault"
    run_log = work / "derivation-runs.jsonl"
    with KnowledgeRepository(database) as repository:
        first_index = KnowledgeIndexer(repository).build(raw, strict=True)
        document_count = repository.count_documents()
        membership_count = int(
            repository.connection.execute("SELECT COUNT(*) FROM source_memberships").fetchone()[0]
        )
    if first_index.failed:
        failures.append("index_build_failed")
    if document_count == 0:
        failures.append("no_supported_documents")

    responses, empty_source_jobs = _pending_responses(database)
    if empty_source_jobs:
        failures.append("documents_without_source_text")
        skips.append("deterministic_derivation_skipped_empty_source_text")
    first_derivation = DerivationPipeline(
        database,
        FakeDerivationProvider(responses),
        run_log=run_log,
        worker_id="acceptance-worker",
        retry_delays=(),
    ).run(limit=max(1, len(responses) + 1))
    if first_derivation.failed or first_derivation.succeeded != len(responses):
        failures.append("deterministic_derivation_failed")

    search_checks_run = len(accepted_queries)
    search_checks_passed = 0
    if accepted_queries:
        search = SearchIndex(database)
        try:
            for query in accepted_queries:
                if query.strip() and search.search(query, limit=1):
                    search_checks_passed += 1
        finally:
            search.close()
        if search_checks_passed != search_checks_run:
            failures.append("accepted_search_check_failed")
    else:
        skips.append("accepted_search_queries_not_provided")

    files = build_generated_files(database, vault)
    exporter = VaultExporter(files, renderer_version=RENDERER_VERSION)
    first_export = exporter.export(vault)
    first_check = exporter.check(vault)
    if first_export.conflicts or not first_check.ok:
        failures.append("vault_projection_failed")

    with KnowledgeRepository(database) as repository:
        second_index = KnowledgeIndexer(repository).build(raw, strict=True)
    second_derivation = DerivationPipeline(
        database,
        FakeDerivationProvider([]),
        run_log=run_log,
        worker_id="acceptance-worker-second-pass",
        retry_delays=(),
    ).run(limit=max(1, document_count + 1))
    second_files = build_generated_files(database, vault)
    second_exporter = VaultExporter(second_files, renderer_version=RENDERER_VERSION)
    second_export = second_exporter.export(vault)
    second_check = second_exporter.check(vault)
    second_pass_zero_work = not any(
        (
            second_index.created,
            second_index.updated,
            second_index.derivation_jobs_queued,
            second_index.search_projections_updated,
            second_derivation.processed,
            second_export.written,
            second_export.conflicts,
            second_export.stale,
        )
    )
    if not second_pass_zero_work:
        failures.append("second_pass_created_work")
    if not second_check.ok:
        failures.append("second_vault_check_failed")

    raw_after_map = _evidence_files(raw)
    frozen_after_map = _evidence_files(frozen)
    raw_after = _map_hash(raw_after_map)
    frozen_after = _map_hash(frozen_after_map)
    if raw_before_map != raw_after_map:
        failures.append("raw_evidence_mutated")
    if frozen_before_map != frozen_after_map:
        failures.append("frozen_evidence_mutated")

    with closing(sqlite3.connect(database)) as connection:
        derivation_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM derivations WHERE kind='article' AND status='accepted'"
            ).fetchone()[0]
        )
    return AcceptanceResult(
        status="failed" if failures else "ok",
        logical_hash=_logical_hash(database, vault),
        raw_hash_before=raw_before,
        raw_hash_after=raw_after,
        frozen_hash_before=frozen_before,
        frozen_hash_after=frozen_after,
        document_count=document_count,
        membership_count=membership_count,
        derivation_count=derivation_count,
        generated_file_count=len(second_files),
        search_checks_run=search_checks_run,
        search_checks_passed=search_checks_passed,
        search_benchmark_reference=SEARCH_BENCHMARK_REFERENCE,
        vault_check_ok=first_check.ok and second_check.ok,
        second_pass_zero_work=second_pass_zero_work,
        failures=tuple(sorted(set(failures))),
        skips=tuple(sorted(set(skips))),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--frozen-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--accepted-query", action="append", default=[])
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="pkb-acceptance-") as temporary:
        result = run_acceptance(
            args.raw_dir,
            Path(temporary),
            frozen_dir=args.frozen_dir,
            accepted_queries=tuple(args.accepted_query),
        )
    result.write(args.output)
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
