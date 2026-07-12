from __future__ import annotations

from dataclasses import dataclass
import json

from pkb.checkpoint import CheckpointStore
from pkb.jsonl import JsonlWriter
from pkb.zhihu import ZhihuClient


@dataclass(frozen=True)
class ExportResult:
    exported: int
    skipped: int


def export_zhihu_collection(
    collection_url: str,
    client: ZhihuClient,
    writer: JsonlWriter,
    checkpoint_store: CheckpointStore,
) -> ExportResult:
    checkpoint = checkpoint_store.load()
    exported_now = 0
    skipped_now = 0
    current_offset = checkpoint.offset
    seen_ids = _existing_article_ids(writer)
    total_exported = max(checkpoint.exported, len(seen_ids))

    for article in client.iter_collection(collection_url, offset=checkpoint.offset):
        article_id = str(article.get("id", ""))
        if article_id in seen_ids:
            skipped_now += 1
        else:
            writer.write(article)
            seen_ids.add(article_id)
            exported_now += 1
            total_exported += 1
        current_offset += 1
        checkpoint_store.save(offset=current_offset, exported=total_exported)

    return ExportResult(exported=exported_now, skipped=skipped_now)


def export_zhihu_author_articles(
    author_url: str,
    client: ZhihuClient,
    writer: JsonlWriter,
    checkpoint_store: CheckpointStore,
) -> ExportResult:
    checkpoint = checkpoint_store.load()
    exported_now = 0
    skipped_now = 0
    current_offset = checkpoint.offset
    seen_ids = _existing_article_ids(writer)
    total_exported = max(checkpoint.exported, len(seen_ids))

    for article in client.iter_member_articles(author_url, offset=checkpoint.offset):
        article_id = str(article.get("id", ""))
        if article_id in seen_ids:
            skipped_now += 1
        else:
            writer.write(article)
            seen_ids.add(article_id)
            exported_now += 1
            total_exported += 1
        current_offset += 1
        checkpoint_store.save(offset=current_offset, exported=total_exported)

    return ExportResult(exported=exported_now, skipped=skipped_now)


def _existing_article_ids(writer: JsonlWriter) -> set[str]:
    path = writer.output_path
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        article_id = record.get("id")
        if article_id:
            ids.add(str(article_id))
    return ids
