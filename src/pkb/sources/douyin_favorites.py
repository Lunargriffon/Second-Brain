"""Normalize locally transcribed Douyin favorites for the knowledge index."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pkb.knowledge.models import NormalizedDocument, SourceMembership


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _timestamp(seconds: object) -> str:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 0:
        return ""
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _content(record: Mapping[str, object]) -> str:
    parts: list[str] = []
    caption = _text(record.get("caption"))
    if caption:
        parts.append(caption)
    hashtags = record.get("hashtags")
    if isinstance(hashtags, list):
        rendered = " ".join(f"#{tag}" for tag in hashtags if isinstance(tag, str) and tag)
        if rendered:
            parts.append(rendered)
    rendered_segments: list[str] = []
    segments = record.get("transcript_segments")
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            text = _text(segment.get("text"))
            start = segment.get("start")
            end = segment.get("end")
            start_stamp = _timestamp(start)
            end_stamp = _timestamp(end)
            ordered = (
                isinstance(start, (int, float))
                and not isinstance(start, bool)
                and isinstance(end, (int, float))
                and not isinstance(end, bool)
                and end >= start
            )
            if text and start_stamp and end_stamp and ordered:
                rendered_segments.append(f"{start_stamp}-{end_stamp} {text}")
        if rendered_segments:
            parts.append("\n".join(rendered_segments))
    if not rendered_segments:
        transcript = _text(record.get("transcript_text"))
        if transcript:
            parts.append(transcript)
    return "\n\n".join(parts)


class DouyinFavoritesAdapter:
    source_name = "douyin"

    def normalize(
        self, record: Mapping[str, object], *, raw_path: Path, raw_line: int
    ) -> NormalizedDocument:
        work_id = _text(record.get("work_id"))
        if not work_id:
            raise ValueError("stable source identity is required")
        canonical_url = f"https://www.douyin.com/video/{work_id}"
        return NormalizedDocument(
            identity_key=f"douyin:work:{work_id}",
            canonical_url=canonical_url,
            title=_text(record.get("caption")),
            author=_text(record.get("author")),
            plain_content=_content(record),
            media_urls=(),
            source_created_at=_text(record.get("published_at")) or None,
            source_observed_at=_text(record.get("observed_at")) or None,
            membership=SourceMembership(
                source=self.source_name,
                source_item_id=work_id,
                collection_id="favorites",
                source_url=canonical_url,
                raw_path=raw_path,
                raw_line=raw_line,
            ),
        )
