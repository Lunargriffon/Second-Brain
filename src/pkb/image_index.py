from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_zhihu_image_index(*, manifest_path: Path, triage_report_path: Path) -> dict[str, Any]:
    rows = _read_manifest_rows(manifest_path)
    articles_by_id: dict[str, dict[str, Any]] = {}

    for row in rows:
        article_id = str(row.get("article_id", ""))
        if not article_id:
            continue
        article = articles_by_id.setdefault(
            article_id,
            {
                "article_id": article_id,
                "article_title": row.get("article_title", ""),
                "article_url": row.get("article_url", ""),
                "images": [],
            },
        )
        article["images"].append(
            {
                "path": row.get("path", ""),
                "image_url": row.get("image_url", ""),
                "bucket": row.get("bucket", ""),
                "reasons": row.get("reasons", []),
            }
        )

    saved_urls = {str(row.get("image_url", "")) for row in rows}
    missing_candidates = [
        {
            "article_id": item.get("article_id", ""),
            "article_title": item.get("article_title", ""),
            "article_url": item.get("article_url", ""),
            "image_url": item.get("image_url", ""),
            "bucket": item.get("bucket", ""),
            "reasons": item.get("reasons", []),
        }
        for item in _eligible_candidates(triage_report_path)
        if item.get("image_url") not in saved_urls
    ]

    articles = sorted(articles_by_id.values(), key=lambda article: article["article_id"])
    return {
        "summary": {
            "articles": len(articles),
            "images": len(rows),
            "missing_candidates": len(missing_candidates),
        },
        "articles": articles,
        "missing_candidates": missing_candidates,
    }


def write_zhihu_image_index(*, manifest_path: Path, triage_report_path: Path, output_path: Path) -> dict[str, Any]:
    index = build_zhihu_image_index(manifest_path=manifest_path, triage_report_path=triage_report_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def _read_manifest_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _eligible_candidates(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    seen_urls: set[str] = set()
    items: list[dict[str, Any]] = []
    for item in report.get("items", []):
        url = item.get("image_url")
        if not isinstance(url, str) or not url:
            continue
        if item.get("bucket") != "keep_knowledge":
            continue
        if url.split("?", 1)[0].lower().endswith(".gif"):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        items.append(item)
    return items
