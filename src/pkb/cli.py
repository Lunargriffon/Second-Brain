from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from pkb.derive.provider import DerivationProvider

from pkb.audit import AuditResult, audit_zhihu_collection
from pkb.config import ConfigError, load_config
from pkb.checkpoint import CheckpointStore
from pkb.exporter import export_zhihu_author_articles, export_zhihu_collection
from pkb.image_index import write_zhihu_image_index
from pkb.image_triage import write_image_triage_report
from pkb.images import download_zhihu_images, download_zhihu_triage_images
from pkb.jsonl import JsonlWriter
from pkb.verify import verify_zhihu_exports
from pkb.zhihu import FakeZhihuClient, RealZhihuClient, ZhihuClientError


def main(argv: Sequence[str] | None = None, *, provider: DerivationProvider | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    if args.command == "export" and args.source == "zhihu":
        client = _build_zhihu_client(args)
        try:
            export_zhihu_collection(
                collection_url=args.collection_url,
                client=client,
                writer=JsonlWriter(Path(args.output)),
                checkpoint_store=CheckpointStore(Path(args.state)),
            )
            return 0
        except ZhihuClientError as exc:
            print(f"Zhihu export stopped: {exc}")
            return 1

    if args.command == "export" and args.source == "zhihu-author":
        client = _build_zhihu_client(args)
        try:
            export_zhihu_author_articles(
                author_url=args.author_url,
                client=client,
                writer=JsonlWriter(Path(args.output)),
                checkpoint_store=CheckpointStore(Path(args.state)),
            )
            return 0
        except ZhihuClientError as exc:
            print(f"Zhihu author export stopped: {exc}")
            return 1

    if args.command == "export" and args.source == "zhihu-batch":
        return _run_zhihu_batch(args)

    if args.command == "audit" and args.source == "zhihu":
        return _run_zhihu_audit(args)

    if args.command == "verify" and args.source == "zhihu":
        return _run_zhihu_verify(args)

    if args.command == "images" and args.source == "zhihu":
        return _run_zhihu_images(args)

    if args.command == "images" and args.source == "zhihu-triage":
        return _run_zhihu_image_triage(args)

    if args.command == "images" and args.source == "zhihu-triage-download":
        return _run_zhihu_triage_image_download(args)

    if args.command == "images" and args.source == "zhihu-index":
        return _run_zhihu_image_index(args)

    if args.command == "index":
        return _run_knowledge_index(args)

    if args.command == "search":
        return _run_knowledge_search(args)

    if args.command == "derive":
        return _run_derivation(args, provider)

    if args.command == "wiki":
        return _run_wiki(args)

    if args.command == "review":
        return _run_review(args)

    parser.error("unsupported command")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkb")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_subparsers = export_parser.add_subparsers(dest="source", required=True)
    audit_parser = subparsers.add_parser("audit")
    audit_subparsers = audit_parser.add_subparsers(dest="source", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_subparsers = verify_parser.add_subparsers(dest="source", required=True)
    images_parser = subparsers.add_parser("images")
    images_subparsers = images_parser.add_subparsers(dest="source", required=True)
    _add_knowledge_parsers(subparsers)
    _add_derivation_parsers(subparsers)
    _add_wiki_parsers(subparsers)
    _add_review_parsers(subparsers)

    zhihu_parser = export_subparsers.add_parser("zhihu")
    zhihu_parser.add_argument("--collection-url", required=True)
    zhihu_parser.add_argument("--output", default="data/raw/zhihu.jsonl")
    zhihu_parser.add_argument("--state", default="data/state/zhihu.state.json")
    zhihu_parser.add_argument("--fixture")
    zhihu_parser.add_argument("--cookie")
    zhihu_parser.add_argument("--env", default=".env")
    zhihu_parser.add_argument("--limit", type=int, default=5)
    zhihu_parser.add_argument("--request-delay", type=float, default=2.0)
    zhihu_parser.add_argument("--max-retry", type=int, default=3)

    zhihu_author_parser = export_subparsers.add_parser("zhihu-author")
    zhihu_author_parser.add_argument("--author-url", required=True)
    zhihu_author_parser.add_argument("--output", default="data/raw/zhihu-author.jsonl")
    zhihu_author_parser.add_argument("--state", default="data/state/zhihu-author.state.json")
    zhihu_author_parser.add_argument("--cookie")
    zhihu_author_parser.add_argument("--env", default=".env")
    zhihu_author_parser.add_argument("--limit", type=int, default=5)
    zhihu_author_parser.add_argument("--request-delay", type=float, default=2.0)
    zhihu_author_parser.add_argument("--max-retry", type=int, default=3)

    batch_parser = export_subparsers.add_parser("zhihu-batch")
    batch_parser.add_argument("--collections-file", required=True)
    batch_parser.add_argument("--output-dir", default="data/raw")
    batch_parser.add_argument("--state-dir", default="data/state")
    batch_parser.add_argument("--fixture")
    batch_parser.add_argument("--cookie")
    batch_parser.add_argument("--env", default=".env")
    batch_parser.add_argument("--limit", type=int, default=5)
    batch_parser.add_argument("--request-delay", type=float, default=2.0)
    batch_parser.add_argument("--max-retry", type=int, default=3)

    audit_zhihu_parser = audit_subparsers.add_parser("zhihu")
    audit_zhihu_parser.add_argument("--collections-file", required=True)
    audit_zhihu_parser.add_argument("--fixture")
    audit_zhihu_parser.add_argument("--cookie")
    audit_zhihu_parser.add_argument("--env", default=".env")
    audit_zhihu_parser.add_argument("--request-delay", type=float, default=2.0)
    audit_zhihu_parser.add_argument("--report")

    verify_zhihu_parser = verify_subparsers.add_parser("zhihu")
    verify_zhihu_parser.add_argument("--audit", required=True)
    verify_zhihu_parser.add_argument("--raw-dir", default="data/raw")
    images_zhihu_parser = images_subparsers.add_parser("zhihu")
    images_zhihu_parser.add_argument("--raw", required=True)
    images_zhihu_parser.add_argument("--output-dir", required=True)
    images_zhihu_parser.add_argument("--state")
    images_zhihu_parser.add_argument("--state-dir", default="data/state")
    images_zhihu_parser.add_argument("--limit", type=int, default=20)
    images_zhihu_parser.add_argument("--request-delay", type=float, default=2.0)
    images_triage_parser = images_subparsers.add_parser("zhihu-triage")
    images_triage_parser.add_argument("--raw-dir", default="data/raw")
    images_triage_parser.add_argument("--report", default="data/state/zhihu-image-triage.json")
    images_triage_download_parser = images_subparsers.add_parser("zhihu-triage-download")
    images_triage_download_parser.add_argument("--report", default="data/state/zhihu-image-triage.json")
    images_triage_download_parser.add_argument("--output-dir", default="data/images/zhihu/knowledge")
    images_triage_download_parser.add_argument("--manifest", default="data/state/zhihu-image-manifest.jsonl")
    images_triage_download_parser.add_argument("--state", default="data/state/zhihu-triage-images.state.json")
    images_triage_download_parser.add_argument("--bucket", action="append", default=["keep_knowledge"])
    images_triage_download_parser.add_argument("--include-gifs", action="store_true")
    images_triage_download_parser.add_argument("--limit", type=int, default=20)
    images_triage_download_parser.add_argument("--request-delay", type=float, default=2.0)
    images_index_parser = images_subparsers.add_parser("zhihu-index")
    images_index_parser.add_argument("--manifest", default="data/state/zhihu-image-manifest.jsonl")
    images_index_parser.add_argument("--report", default="data/state/zhihu-image-triage.json")
    images_index_parser.add_argument("--output", default="data/state/zhihu-image-article-index.json")
    return parser


def _add_knowledge_parsers(subparsers: argparse._SubParsersAction) -> None:
    index_parser = subparsers.add_parser("index")
    index_subparsers = index_parser.add_subparsers(dest="index_command", required=True)
    for command in ("build", "rebuild"):
        command_parser = index_subparsers.add_parser(command)
        command_parser.add_argument("--raw-dir", required=True)
        command_parser.add_argument("--db", required=True)
        command_parser.add_argument("--report")
        if command == "build":
            command_parser.add_argument("--strict", action="store_true")

    status_parser = index_subparsers.add_parser("status")
    status_parser.add_argument("--db", required=True)
    status_parser.add_argument("--format", choices=("text", "json"), default="text")

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--db", required=True)
    search_parser.add_argument("--source")
    search_parser.add_argument("--collection")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--format", choices=("text", "json"), default="text")


def _bounded(parser: argparse.ArgumentParser, *, dry_run: bool = False) -> None:
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--limit", type=int, default=None)
    scope.add_argument("--unlimited", action="store_true")
    if dry_run:
        parser.add_argument("--dry-run", action="store_true")


def _add_derivation_parsers(subparsers: argparse._SubParsersAction) -> None:
    derive = subparsers.add_parser("derive")
    commands = derive.add_subparsers(dest="derive_command", required=True)
    articles = commands.add_parser("articles")
    articles.add_argument("--db", required=True)
    articles.add_argument("--env", default=".env")
    articles.add_argument("--report", default="data/derived/runs.jsonl")
    _bounded(articles, dry_run=True)
    relations = commands.add_parser("relations")
    relations.add_argument("--db", required=True)
    relations.add_argument("--env", default=".env")
    relations.add_argument("--report", default="data/derived/relation-runs.jsonl")
    relations.add_argument("--document-limit", type=int)
    relations.add_argument("--per-document-limit", type=int)
    relations.add_argument("--pair-limit", type=int)
    relations.add_argument("--unlimited", action="store_true")
    relations.add_argument("--dry-run", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("--db", required=True)
    status.add_argument("--format", choices=("text", "json"), default="text")
    retry = commands.add_parser("retry")
    retry.add_argument("--db", required=True)
    retry.add_argument("--status", choices=("failed",), required=True)
    _bounded(retry)
    migrate_parser = commands.add_parser("migrate")
    migrate_parser.add_argument("--db", required=True)
    migrate_parser.add_argument("--normalization-version", type=int, required=True)
    _bounded(migrate_parser, dry_run=True)


def _add_wiki_parsers(subparsers: argparse._SubParsersAction) -> None:
    wiki = subparsers.add_parser("wiki")
    commands = wiki.add_subparsers(dest="wiki_command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--db", required=True)
    export.add_argument("--vault", required=True)
    export.add_argument("--copy-attachments", action="store_true")
    export.add_argument("--report")
    check = commands.add_parser("check")
    check.add_argument("--vault", required=True)
    check.add_argument("--format", choices=("text", "json"), default="text")
    clean = commands.add_parser("clean")
    clean.add_argument("--vault", required=True)
    clean.add_argument("--stale-only", action="store_true", required=True)
    clean.add_argument("--dry-run", action="store_true")


def _add_review_parsers(subparsers: argparse._SubParsersAction) -> None:
    review = subparsers.add_parser("review")
    commands = review.add_subparsers(dest="review_command", required=True)
    today = commands.add_parser("today")
    today.add_argument("--db", required=True)
    today.add_argument("--count", type=int, default=5)
    today.add_argument("--date")
    today.add_argument("--vault")
    mark = commands.add_parser("mark")
    mark.add_argument("document_id", metavar="DOCUMENT_ID")
    mark.add_argument("--db", required=True)
    mark.add_argument("--status", choices=("read", "queued", "ignored"), required=True)
    mark.add_argument("--date")


def _build_zhihu_client(args: argparse.Namespace) -> FakeZhihuClient | RealZhihuClient:
    fixture = getattr(args, "fixture", None)
    if fixture:
        return FakeZhihuClient(Path(fixture))
    cookie = args.cookie
    if cookie is None:
        try:
            config = load_config(Path(args.env))
            cookie = config.zhihu_cookie
        except ConfigError:
            cookie = ""
    max_items = None if args.limit == 0 else args.limit
    return RealZhihuClient(
        cookie=cookie,
        request_delay=args.request_delay,
        max_retry=args.max_retry,
        max_items=max_items,
    )


def _run_zhihu_batch(args: argparse.Namespace) -> int:
    urls = _read_collection_urls(Path(args.collections_file))
    if not urls:
        print("No Zhihu collection URLs found.")
        return 1

    for url in urls:
        collection_id = _collection_id_from_url(url)
        client = _build_zhihu_client(args)
        try:
            export_zhihu_collection(
                collection_url=url,
                client=client,
                writer=JsonlWriter(Path(args.output_dir) / f"zhihu-{collection_id}.jsonl"),
                checkpoint_store=CheckpointStore(Path(args.state_dir) / f"zhihu-{collection_id}.state.json"),
            )
        except ZhihuClientError as exc:
            print(f"Zhihu export stopped for {collection_id}: {exc}")
            return 1
    return 0


def _read_collection_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def _collection_id_from_url(url: str) -> str:
    match = re.search(r"/collection/(\d+)", url)
    if not match:
        raise ZhihuClientError(f"Could not find collection id in URL: {url}")
    return match.group(1)


def _run_zhihu_audit(args: argparse.Namespace) -> int:
    urls = _read_collection_urls(Path(args.collections_file))
    if not urls:
        print("No Zhihu collection URLs found.")
        return 1

    report_file = None
    completed_ids: set[str] = set()
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        completed_ids = _completed_audit_ids(report_path)
        report_file = report_path.open("a", encoding="utf-8")

    try:
        for url in urls:
            collection_id = _collection_id_from_url(url)
            if collection_id in completed_ids:
                continue
            result = _audit_one_url(url, args)
            _print_audit_result(result)
            if report_file:
                report_file.write(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
                report_file.flush()
    except ZhihuClientError as exc:
        print(f"Zhihu audit stopped: {exc}")
        return 1
    finally:
        if report_file:
            report_file.close()
    return 0


def _print_audit_result(result: AuditResult) -> None:
        warnings = ",".join(result.warnings) if result.warnings else "-"
        print(
            f"{result.collection_id}: reported={result.reported_total} "
            f"scanned={result.scanned_total} pages={result.page_count} "
            f"types={result.type_counts} warnings={warnings}"
        )


def _audit_one_url(url: str, args: argparse.Namespace) -> AuditResult:
    if args.fixture:
        return _audit_fixture(url, Path(args.fixture))
    cookie = args.cookie
    if cookie is None:
        config = load_config(Path(args.env))
        cookie = config.zhihu_cookie
    return audit_zhihu_collection(url, cookie=cookie, request_delay=args.request_delay)


def _completed_audit_ids(report_path: Path) -> set[str]:
    if not report_path.exists():
        return set()
    completed: set[str] = set()
    for line in report_path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        collection_id = record.get("collection_id")
        if collection_id:
            completed.add(str(collection_id))
    return completed


def _audit_fixture(url: str, fixture_path: Path) -> AuditResult:
    import json

    collection_id = _collection_id_from_url(url)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    type_counts: dict[str, int] = {}
    for item in payload.get("items", []):
        item_type = item.get("type", "answer")
        type_counts[item_type] = type_counts.get(item_type, 0) + 1
    total = len(payload.get("items", []))
    return AuditResult(
        collection_id=collection_id,
        reported_total=total,
        scanned_total=total,
        type_counts=type_counts,
        page_count=1,
        warnings=[],
    )


def _run_zhihu_verify(args: argparse.Namespace) -> int:
    results = verify_zhihu_exports(Path(args.audit), Path(args.raw_dir))
    exit_code = 0
    for result in results:
        print(
            f"{result.collection_id} {result.status} "
            f"scanned={result.scanned_total} exported={result.exported_total}"
        )
        if result.status != "ok":
            exit_code = 1
    return exit_code


def _run_zhihu_images(args: argparse.Namespace) -> int:
    if args.request_delay < 2:
        print("Zhihu image download requires --request-delay >= 2.")
        return 1
    raw_path = Path(args.raw)
    state_path = Path(args.state) if args.state else _default_image_state_path(raw_path, Path(args.state_dir))
    result = download_zhihu_images(
        raw_path=raw_path,
        output_dir=Path(args.output_dir),
        checkpoint_store=CheckpointStore(state_path),
        limit=args.limit,
        request_delay=args.request_delay,
    )
    print(
        f"images downloaded={result.downloaded} skipped={result.skipped} "
        f"stopped={result.stopped_reason or '-'}"
    )
    return 1 if result.stopped_reason else 0


def _run_zhihu_image_triage(args: argparse.Namespace) -> int:
    report = write_image_triage_report(Path(args.raw_dir), Path(args.report))
    print(
        f"image triage images={report['summary']['images_total']} "
        f"articles={report['summary']['articles_with_images']} report={args.report}"
    )
    return 0


def _run_zhihu_triage_image_download(args: argparse.Namespace) -> int:
    if args.request_delay < 2:
        print("Zhihu image download requires --request-delay >= 2.")
        return 1
    result = download_zhihu_triage_images(
        report_path=Path(args.report),
        output_dir=Path(args.output_dir),
        manifest_path=Path(args.manifest),
        checkpoint_store=CheckpointStore(Path(args.state)),
        include_buckets=set(args.bucket),
        include_gifs=args.include_gifs,
        limit=args.limit,
        request_delay=args.request_delay,
    )
    print(
        f"triage images downloaded={result.downloaded} skipped={result.skipped} "
        f"stopped={result.stopped_reason or '-'}"
    )
    return 1 if result.stopped_reason else 0


def _run_zhihu_image_index(args: argparse.Namespace) -> int:
    index = write_zhihu_image_index(
        manifest_path=Path(args.manifest),
        triage_report_path=Path(args.report),
        output_path=Path(args.output),
    )
    print(
        f"image index articles={index['summary']['articles']} "
        f"images={index['summary']['images']} "
        f"missing={index['summary']['missing_candidates']} output={args.output}"
    )
    return 0


def _run_knowledge_index(args: argparse.Namespace) -> int:
    from pkb.knowledge.indexer import IndexBuildError, KnowledgeIndexer
    from pkb.knowledge.repository import KnowledgeRepository
    from pkb.knowledge.search import SearchIndex

    with KnowledgeRepository(Path(args.db)) as repository:
        if args.index_command == "status":
            status = {"documents": repository.count_documents()}
            if args.format == "json":
                print(json.dumps(status, ensure_ascii=False, sort_keys=True))
            else:
                print(f"documents={status['documents']}")
            return 0

        try:
            report = KnowledgeIndexer(repository).build(
                Path(args.raw_dir), strict=getattr(args, "strict", False)
            )
        except IndexBuildError as exc:
            print(f"Index build stopped: {exc}")
            return 1
        if args.index_command == "rebuild":
            SearchIndex(repository).rebuild()
        if args.report:
            report.write(Path(args.report))
        print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0


def _run_knowledge_search(args: argparse.Namespace) -> int:
    from pkb.knowledge.search import SearchIndex

    index = SearchIndex(Path(args.db))
    try:
        results = index.search(
            args.query,
            source=args.source,
            collection_id=args.collection,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"Search stopped: {exc}")
        return 1
    finally:
        index.close()
    if args.format == "json":
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False))
    else:
        for result in results:
            print(f"{result.document_id}\t{result.title}\t{result.url}\n{result.snippet}")
    return 0


def _effective_limit(args: argparse.Namespace, *, default: int = 10) -> int | None:
    if args.unlimited:
        return None
    limit = args.limit if args.limit is not None else default
    if limit <= 0:
        raise ValueError("limit must be positive")
    return limit


def _run_derivation(args: argparse.Namespace, provider: DerivationProvider | None) -> int:
    from pkb.derive.pipeline import DerivationPipeline
    from pkb.derive.prompts import ARTICLE_PROMPT_VERSION
    from pkb.derive.workflows import (
        article_scope,
        job_status,
        migration_candidates,
        queue_migration,
        retry_failed,
    )

    if args.derive_command == "status":
        status = job_status(Path(args.db))
        if args.format == "json":
            print(json.dumps(status, sort_keys=True))
        else:
            print(" ".join(f"{key}={value}" for key, value in status.items()))
        return 0
    if args.derive_command == "relations":
        return _run_relation_derivation(args, provider)
    try:
        limit = _effective_limit(args)
    except ValueError as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2

    if args.derive_command == "retry":
        # An unbounded retry still has an explicitly inspected finite database scope.
        retry_limit = limit if limit is not None else 2**63 - 1
        count = retry_failed(Path(args.db), retry_limit)
        print(f"retried={count}")
        return 0

    if args.derive_command == "migrate":
        if args.normalization_version <= 0:
            print("normalization version must be positive", file=__import__("sys").stderr)
            return 2
        candidates = migration_candidates(Path(args.db), args.normalization_version, limit)
        if args.dry_run:
            print(f"would_queue={len(candidates)} normalization_version={args.normalization_version}")
            return 0
        queued = queue_migration(Path(args.db), args.normalization_version, limit)
        print(f"queued={queued} normalization_version={args.normalization_version}")
        return 0

    try:
        provider = provider or _build_derivation_provider(Path(args.env))
    except (ConfigError, ValueError):
        print("Derivation provider configuration is missing or invalid.", file=__import__("sys").stderr)
        return 1
    scope = article_scope(Path(args.db), limit)
    print(
        f"documents={scope.documents} model={provider.model_name} "
        f"prompt_version={ARTICLE_PROMPT_VERSION} "
        f"estimated_input_chars={scope.estimated_input_chars}"
    )
    if args.dry_run or scope.documents == 0:
        return 0
    result = DerivationPipeline(
        Path(args.db), provider, run_log=Path(args.report)
    ).run(limit=scope.documents)
    print(
        f"processed={result.processed} succeeded={result.succeeded} "
        f"failed={result.failed} stopped={str(result.stopped).lower()}"
    )
    return 1 if result.stopped else 0


def _run_relation_derivation(
    args: argparse.Namespace, provider: DerivationProvider | None
) -> int:
    from pkb.derive.prompts import RELATION_PROMPT_VERSION
    from pkb.derive.relations import (
        MAX_DOCUMENT_LIMIT,
        MAX_PAIR_LIMIT,
        MAX_PER_DOCUMENT_LIMIT,
        RelationCandidateBuilder,
        RelationPipeline,
    )

    supplied = (args.document_limit, args.per_document_limit, args.pair_limit)
    if args.unlimited:
        if any(value is not None for value in supplied):
            print("--unlimited cannot be combined with relation limits", file=__import__("sys").stderr)
            return 2
        bounds = (MAX_DOCUMENT_LIMIT, MAX_PER_DOCUMENT_LIMIT, MAX_PAIR_LIMIT)
    else:
        if any(value is None for value in supplied):
            print(
                "document-limit, per-document-limit, and pair-limit are required without --unlimited",
                file=__import__("sys").stderr,
            )
            return 2
        bounds = supplied
    document_limit, per_document_limit, pair_limit = bounds
    try:
        with RelationCandidateBuilder(Path(args.db), read_only=args.dry_run) as builder:
            preview = builder.dry_run(
                document_limit=document_limit,
                per_document_limit=per_document_limit,
                pair_limit=pair_limit,
            )
    except ValueError as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2
    print(
        f"documents={preview.documents_considered} pairs={preview.pair_count} "
        f"prompt_version={RELATION_PROMPT_VERSION}"
    )
    if args.dry_run or preview.pair_count == 0:
        return 0
    try:
        provider = provider or _build_derivation_provider(Path(args.env))
    except (ConfigError, ValueError):
        print("Derivation provider configuration is missing or invalid.", file=__import__("sys").stderr)
        return 1
    result = RelationPipeline(
        Path(args.db), provider, run_log=Path(args.report)
    ).run(
        document_limit=document_limit,
        per_document_limit=per_document_limit,
        pair_limit=pair_limit,
    )
    print(
        f"processed={result.processed} accepted={result.accepted} "
        f"no_relation={result.no_relation} invalid={result.invalid} "
        f"failed={result.failed} stopped={str(result.stopped).lower()}"
    )
    return 1 if result.stopped else 0


def _run_wiki(args: argparse.Namespace) -> int:
    from pkb.wiki.exporter import VaultExporter

    vault = Path(args.vault)
    if args.wiki_command == "export":
        from pkb.wiki.projection import RENDERER_VERSION, build_generated_files

        files = build_generated_files(Path(args.db), vault, copy_attachments=args.copy_attachments)
        result = VaultExporter(files, renderer_version=RENDERER_VERSION).export(vault)
        summary = {key: len(getattr(result, key)) for key in ("written", "unchanged", "conflicts", "stale", "removed")}
        if args.report:
            report = Path(args.report)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
        print(" ".join(f"{key}={value}" for key, value in summary.items()))
        return 1 if result.conflicts else 0
    if args.wiki_command == "check":
        report = VaultExporter.inspect(vault)
        summary = {
            "ok": report.ok,
            **{key: len(getattr(report, key)) for key in (
                "broken_links", "creatable_user_notes", "missing_source_ids",
                "manifest_mismatches", "modified", "unmarked_collisions", "stale",
            )},
        }
        if args.format == "json":
            print(json.dumps(summary, sort_keys=True))
        else:
            print(" ".join(f"{key}={str(value).lower() if isinstance(value, bool) else value}" for key, value in summary.items()))
        return 0 if report.ok else 1
    result = VaultExporter.clean_stale(vault, dry_run=args.dry_run)
    print(f"stale={len(result.stale)} removed={len(result.removed)} conflicts={len(result.conflicts)} dry_run={str(args.dry_run).lower()}")
    return 1 if result.conflicts else 0


def _run_review(args: argparse.Namespace) -> int:
    from datetime import date as current_date

    from pkb.review import DailyReviewService, DocumentNotFoundError
    from pkb.wiki.exporter import GeneratedFile, VaultExporter
    from pkb.wiki.projection import RENDERER_VERSION, build_generated_files
    from pkb.wiki.renderer import render_daily_review

    date_text = args.date or current_date.today().isoformat()
    try:
        with DailyReviewService(Path(args.db)) as service:
            if args.review_command == "mark":
                state = service.mark(args.document_id, status=args.status, date=date_text)
                print(
                    f"document_id={args.document_id} status={state.status} "
                    f"last_reviewed={state.last_reviewed}"
                )
                return 0
            items = service.select(date=date_text, count=args.count)
    except (ValueError, DocumentNotFoundError) as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2

    for item in items:
        print(
            f"{item.stable_id}\t{item.title}\ttopic={item.primary_topic}\t"
            f"manual={item.manual_priority if item.manual_priority is not None else '-'}\t"
            f"ai={item.ai_reading_priority}\tevergreen={item.evergreen_score}"
        )
    if args.vault:
        relative = f"daily/{date_text}.md"
        rendered = render_daily_review(date_text, items)
        vault = Path(args.vault)
        files = build_generated_files(Path(args.db), vault)
        files.append(GeneratedFile(relative, rendered, f"daily-{date_text}"))
        result = VaultExporter(
            files,
            renderer_version=RENDERER_VERSION,
        ).export(vault)
        if result.conflicts:
            print(f"refused to overwrite non-generated or modified daily file: {relative}",
                  file=__import__("sys").stderr)
            return 1
        print(f"vault={relative} items={len(items)}")
    return 0


def _build_derivation_provider(env_path: Path) -> DerivationProvider:
    from pkb.config import read_env_file
    from pkb.derive.provider import OpenAICompatibleProvider

    values = {**read_env_file(env_path), **os.environ}
    base_url = values.get("PKB_LLM_BASE_URL", "").strip()
    api_key = values.get("PKB_LLM_API_KEY", "").strip()
    model = values.get("PKB_LLM_MODEL", "").strip()
    if not (base_url and api_key and model):
        raise ConfigError(
            "PKB_LLM_BASE_URL, PKB_LLM_API_KEY, and PKB_LLM_MODEL are required"
        )
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=float(values.get("PKB_LLM_TIMEOUT", "60")),
    )


def _default_image_state_path(raw_path: Path, state_dir: Path) -> Path:
    match = re.search(r"zhihu-(\d+)", raw_path.stem)
    suffix = match.group(1) if match else raw_path.stem
    return state_dir / f"images-{suffix}.state.json"


if __name__ == "__main__":
    raise SystemExit(main())
