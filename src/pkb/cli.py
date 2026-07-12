from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence

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


def main(argv: Sequence[str] | None = None) -> int:
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


def _default_image_state_path(raw_path: Path, state_dir: Path) -> Path:
    match = re.search(r"zhihu-(\d+)", raw_path.stem)
    suffix = match.group(1) if match else raw_path.stem
    return state_dir / f"images-{suffix}.state.json"


if __name__ == "__main__":
    raise SystemExit(main())
