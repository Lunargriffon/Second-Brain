"""Deterministic knowledge-value classification for Douyin metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Protocol
import unicodedata


class Eligibility(str, Enum):
    KEEP = "keep"
    EXCLUDE = "exclude"


@dataclass(frozen=True)
class VideoMetadata:
    caption: str
    hashtags: tuple[str, ...]
    author: str


@dataclass(frozen=True)
class EligibilityDecision:
    eligibility: Eligibility
    reasons: tuple[str, ...]
    classifier_version: str
    input_hash: str


class KnowledgeValueClassifier(Protocol):
    version: str

    def classify(self, metadata: VideoMetadata) -> EligibilityDecision: ...


_KNOWLEDGE_SIGNALS: dict[str, tuple[str, ...]] = {
    "knowledge_tutorial": (
        "教程", "教学", "如何", "怎么", "入门", "实操", "手把手", "course",
        "tutorial", "howto",
    ),
    "knowledge_method": (
        "方法", "步骤", "技巧", "指南", "参数", "设置", "流程", "框架", "清单",
        "模板", "公式", "原理",
    ),
    "knowledge_analysis": (
        "分析", "拆解", "复盘", "总结", "解读", "研究", "逻辑", "原因", "案例",
        "对比", "测评",
    ),
    "knowledge_experience": (
        "经验", "避坑", "心得", "认知", "观点", "建议", "策略",
    ),
    "knowledge_subject": (
        "编程", "python", "数据库", "人工智能", "ai", "自动化", "商业", "创业",
        "运营", "营销", "产品", "管理", "职场", "学习", "读书", "法律", "历史",
        "科学", "科普", "健康", "医学", "金融", "投资", "经济", "摄影",
    ),
}

_EXCLUSION_SIGNALS: dict[str, tuple[str, ...]] = {
    "appreciation_beauty": (
        "美女", "帅哥", "颜值", "写真", "自拍", "美腿", "身材", "纯欲", "擦边",
        "氛围感美女",
    ),
    "appreciation_scenery": (
        "风景", "壁纸", "治愈系风光", "日落", "晚霞", "云海", "航拍美景",
    ),
    "lifestyle_travel": (
        "旅游攻略", "旅行攻略", "景点推荐", "酒店推荐", "民宿推荐", "城市打卡",
        "探店", "美食打卡",
    ),
    "entertainment_gaming": (
        "五杀", "高光时刻", "游戏高光", "王者荣耀", "原神", "吃鸡", "上分",
    ),
    "entertainment_clip": (
        "影视片段", "电影片段", "电视剧片段", "混剪", "卡点", "舞蹈", "热舞",
        "对口型", "搞笑段子", "纯音乐", "翻唱",
    ),
}


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(normalized.split())


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_normalize(phrase) in text for phrase in phrases)


class RuleBasedKnowledgeValueClassifier:
    """Fail-closed metadata policy for a knowledge-only corpus."""

    version = "douyin-knowledge-rules-v1"

    def classify(self, metadata: VideoMetadata) -> EligibilityDecision:
        normalized_caption = _normalize(metadata.caption)
        normalized_hashtags = tuple(_normalize(tag) for tag in metadata.hashtags)
        normalized_author = _normalize(metadata.author)
        payload = {
            "author": normalized_author,
            "caption": normalized_caption,
            "hashtags": list(normalized_hashtags),
        }
        input_hash = hashlib.sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        searchable = " ".join((normalized_caption, *normalized_hashtags))
        positives = tuple(
            reason
            for reason, phrases in _KNOWLEDGE_SIGNALS.items()
            if _contains_any(searchable, phrases)
        )
        exclusions = tuple(
            reason
            for reason, phrases in _EXCLUSION_SIGNALS.items()
            if _contains_any(searchable, phrases)
        )

        if exclusions and len(positives) < 2:
            eligibility = Eligibility.EXCLUDE
            reasons = exclusions
        elif positives:
            eligibility = Eligibility.KEEP
            reasons = positives
        else:
            eligibility = Eligibility.EXCLUDE
            reasons = ("insufficient_knowledge_signal",)
        return EligibilityDecision(
            eligibility=eligibility,
            reasons=reasons,
            classifier_version=self.version,
            input_hash=input_hash,
        )
