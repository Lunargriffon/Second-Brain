from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ImageTriageBucket(StrEnum):
    KEEP_KNOWLEDGE = "keep_knowledge"
    REVIEW_POSSIBLE_KNOWLEDGE = "review_possible_knowledge"
    REVIEW_UNKNOWN = "review_unknown"
    REJECT_DECORATIVE = "reject_decorative"
    REJECT_PRODUCT_OR_SHOPPING = "reject_product_or_shopping"
    REJECT_GAME_ENTERTAINMENT = "reject_game_entertainment"


@dataclass(frozen=True)
class ImageTriageItem:
    collection_id: str
    article_id: str
    article_title: str
    article_url: str
    image_url: str
    bucket: ImageTriageBucket
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "collection_id": self.collection_id,
            "article_id": self.article_id,
            "article_title": self.article_title,
            "article_url": self.article_url,
            "image_url": self.image_url,
            "bucket": self.bucket.value,
            "reasons": list(self.reasons),
        }


STRONG_KNOWLEDGE_KEYWORDS = (
    "图表",
    "表格",
    "流程图",
    "架构图",
    "示意图",
    "脑图",
    "截图",
    "代码",
    "公式",
    "数据",
    "曲线",
    "统计",
    "论文",
    "文献",
    "笔记",
    "书单",
    "书页",
    "课件",
    "PPT",
    "路线图",
    "清单",
    "模型",
    "框架",
    "配置",
    "界面",
    "页面",
)

WEAK_KNOWLEDGE_KEYWORDS = (
    "模型",
    "框架",
    "如图",
    "下图",
    "上图",
    "见图",
)

POSSIBLE_KNOWLEDGE_KEYWORDS = (
    "读博",
    "科研",
    "学习",
    "创业",
    "AI",
    "编程",
    "开发",
    "产品",
    "职业",
    "面试",
    "投资",
    "经济",
    "心理",
    "沟通",
    "认知",
)

DECORATIVE_KEYWORDS = (
    "头像",
    "壁纸",
    "美图",
    "自拍",
    "写真",
    "美女",
    "女生照片",
    "穿搭",
    "服装",
    "发型",
    "表情包",
    "动漫图",
    "插画",
    "背景图",
    "手机拍照",
)

PRODUCT_KEYWORDS = (
    "淘宝",
    "京东",
    "拼多多",
    "1688",
    "购买",
    "买哪",
    "值得买",
    "推荐店铺",
    "好物",
    "商品",
    "链接",
    "优惠券",
    "价格",
    "测评",
    "开箱",
)

GAME_KEYWORDS = (
    "galgame",
    "Galgame",
    "Steam",
    "游戏",
    "手游",
    "主机",
    "二次元",
    "番剧",
    "漫画",
    "电影",
)


def classify_image_record(record: dict[str, Any], *, collection_id: str) -> list[ImageTriageItem]:
    images = [url for url in record.get("images", []) if isinstance(url, str) and url]
    if not images:
        return []

    title = str(record.get("title", ""))
    text = _record_context(record)
    bucket, reasons = _classify_context(text, images)
    title_bucket = _title_reject_bucket(title)
    if title_bucket:
        bucket, reasons = _merge_title_bucket(bucket, reasons, title_bucket)
    return [
        ImageTriageItem(
            collection_id=collection_id,
            article_id=str(record.get("id", "")),
            article_title=str(record.get("title", "")),
            article_url=str(record.get("url", "")),
            image_url=url,
            bucket=bucket,
            reasons=reasons + _url_reasons(url),
        )
        for url in images
    ]


def build_image_triage_report(raw_paths: list[Path]) -> dict[str, Any]:
    items: list[ImageTriageItem] = []
    article_ids_with_images: set[str] = set()

    for raw_path in raw_paths:
        collection_id = _collection_id_from_path(raw_path)
        for record in _iter_records(raw_path):
            record_items = classify_image_record(record, collection_id=collection_id)
            if record_items:
                article_ids_with_images.add(str(record.get("id", "")))
                items.extend(record_items)

    bucket_report = {
        bucket.value: {"images": 0, "articles": 0, "examples": []}
        for bucket in ImageTriageBucket
    }
    bucket_articles: dict[str, set[str]] = {bucket.value: set() for bucket in ImageTriageBucket}

    for item in items:
        data = bucket_report[item.bucket.value]
        data["images"] += 1
        bucket_articles[item.bucket.value].add(item.article_id)
        if len(data["examples"]) < 8:
            data["examples"].append(
                {
                    "title": item.article_title,
                    "article_url": item.article_url,
                    "image_url": item.image_url,
                    "reasons": list(item.reasons),
                }
            )

    for bucket, article_ids in bucket_articles.items():
        bucket_report[bucket]["articles"] = len(article_ids)

    return {
        "summary": {
            "raw_files": len(raw_paths),
            "articles_with_images": len(article_ids_with_images),
            "images_total": len(items),
        },
        "buckets": bucket_report,
        "items": [item.to_dict() for item in items],
    }


def write_image_triage_report(raw_dir: Path, output_path: Path) -> dict[str, Any]:
    raw_paths = sorted(path for path in raw_dir.glob("zhihu-*.jsonl") if ".sample" not in path.name)
    report = build_image_triage_report(raw_paths)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _classify_context(text: str, images: list[str]) -> tuple[ImageTriageBucket, tuple[str, ...]]:
    if _has_any(text, STRONG_KNOWLEDGE_KEYWORDS):
        return ImageTriageBucket.KEEP_KNOWLEDGE, ("knowledge_context",)
    if _has_any(text, DECORATIVE_KEYWORDS):
        return ImageTriageBucket.REJECT_DECORATIVE, ("decorative_context",)
    if _has_any(text, PRODUCT_KEYWORDS):
        return ImageTriageBucket.REJECT_PRODUCT_OR_SHOPPING, ("product_or_shopping_context",)
    if _has_any(text, GAME_KEYWORDS):
        return ImageTriageBucket.REJECT_GAME_ENTERTAINMENT, ("game_entertainment_context",)
    if _has_any(text, WEAK_KNOWLEDGE_KEYWORDS):
        return ImageTriageBucket.REVIEW_POSSIBLE_KNOWLEDGE, ("weak_knowledge_context",)
    if len(images) <= 10 and _has_any(text, POSSIBLE_KNOWLEDGE_KEYWORDS):
        return ImageTriageBucket.REVIEW_POSSIBLE_KNOWLEDGE, ("possible_knowledge_context",)
    return ImageTriageBucket.REVIEW_UNKNOWN, ("unknown_context",)


def _title_reject_bucket(title: str) -> tuple[ImageTriageBucket, tuple[str, ...]] | None:
    if _has_any(title, DECORATIVE_KEYWORDS):
        return ImageTriageBucket.REJECT_DECORATIVE, ("decorative_title",)
    if _has_any(title, PRODUCT_KEYWORDS):
        return ImageTriageBucket.REJECT_PRODUCT_OR_SHOPPING, ("product_or_shopping_title",)
    if _has_any(title, GAME_KEYWORDS):
        return ImageTriageBucket.REJECT_GAME_ENTERTAINMENT, ("game_entertainment_title",)
    return None


def _merge_title_bucket(
    bucket: ImageTriageBucket,
    reasons: tuple[str, ...],
    title_bucket: tuple[ImageTriageBucket, tuple[str, ...]],
) -> tuple[ImageTriageBucket, tuple[str, ...]]:
    new_bucket, title_reasons = title_bucket
    if bucket == new_bucket:
        return bucket, tuple(dict.fromkeys(reasons + title_reasons))
    return new_bucket, title_reasons


def _url_reasons(url: str) -> tuple[str, ...]:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix == ".gif":
        return ("gif_url",)
    return ()


def _record_context(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("title", "")),
        str(record.get("content", ""))[:5000],
        str(record.get("source_collection_title", "")),
    ]
    return "\n".join(parts)


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(keyword.lower() in normalized for keyword in keywords)


def _iter_records(path: Path):
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        yield json.loads(line)


def _collection_id_from_path(path: Path) -> str:
    match = re.search(r"zhihu-(\d+)", path.stem)
    return match.group(1) if match else path.stem
