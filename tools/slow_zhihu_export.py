from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from pkb.checkpoint import CheckpointStore
from pkb.cli import _collection_id_from_url, _read_collection_urls
from pkb.config import load_config
from pkb.exporter import export_zhihu_collection
from pkb.jsonl import JsonlWriter
from pkb.zhihu import RealZhihuClient, ZhihuClientError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collections-file", default="data/config/zhihu-collections.txt")
    parser.add_argument("--audit", default="data/state/zhihu-audit.jsonl")
    parser.add_argument("--output-dir", default="data/raw")
    parser.add_argument("--state-dir", default="data/state")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--request-delay", type=float, default=12.0)
    parser.add_argument("--collection-pause", type=float, default=60.0)
    parser.add_argument("--max-retry", type=int, default=2)
    parser.add_argument("--log", default="data/state/zhihu-slow-export.log")
    args = parser.parse_args()

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    audit_totals = _audit_totals(Path(args.audit))
    cookie = load_config(Path(args.env)).zhihu_cookie
    urls = _read_collection_urls(Path(args.collections_file))

    _log(
        log_path,
        f"START request_delay={args.request_delay} collection_pause={args.collection_pause} max_retry={args.max_retry}",
    )
    for index, url in enumerate(urls, start=1):
        collection_id = _collection_id_from_url(url)
        raw_path = Path(args.output_dir) / f"zhihu-{collection_id}.jsonl"
        state_path = Path(args.state_dir) / f"zhihu-{collection_id}.state.json"
        scanned_total = audit_totals.get(collection_id)
        current_lines = _count_lines(raw_path)
        if scanned_total is not None and current_lines == scanned_total:
            _log(log_path, f"SKIP {collection_id} {index}/{len(urls)} lines={current_lines}")
            continue

        _log(log_path, f"EXPORT {collection_id} {index}/{len(urls)} before={current_lines} scanned={scanned_total}")
        client = RealZhihuClient(
            cookie=cookie,
            request_delay=args.request_delay,
            max_retry=args.max_retry,
            max_items=None,
        )
        try:
            result = export_zhihu_collection(
                collection_url=url,
                client=client,
                writer=JsonlWriter(raw_path),
                checkpoint_store=CheckpointStore(state_path),
            )
        except ZhihuClientError as exc:
            _log(log_path, f"STOP {collection_id} error={exc}")
            return 1

        after_lines = _count_lines(raw_path)
        _log(
            log_path,
            f"DONE {collection_id} exported={result.exported} skipped={result.skipped} after={after_lines}",
        )
        if index < len(urls):
            time.sleep(args.collection_pause)

    _log(log_path, "COMPLETE")
    return 0


def _audit_totals(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    totals: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        totals[str(record["collection_id"])] = int(record["scanned_total"])
    return totals


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip())


def _log(path: Path, message: str) -> None:
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with path.open("a", encoding="utf-8") as file:
        file.write(f"{timestamp} {message}\n")
        file.flush()


if __name__ == "__main__":
    raise SystemExit(main())
