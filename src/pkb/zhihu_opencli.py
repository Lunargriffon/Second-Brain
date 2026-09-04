"""Zhihu collection client backed by an authenticated OpenCLI browser session."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any, Iterable, Mapping

from pkb.opencli_gateway import OpenCliError, OpenCliGateway
from pkb.zhihu import ZhihuClientError


class OpenCliZhihuClient:
    def __init__(self, *, gateway: OpenCliGateway | None = None, max_items: int | None = None) -> None:
        self.gateway = gateway or OpenCliGateway()
        self.max_items = max_items

    def iter_collection(self, collection_url: str, offset: int = 0) -> Iterable[dict[str, Any]]:
        match = re.search(r"/collection/(\d+)", collection_url)
        if not match:
            raise ZhihuClientError("Could not find Zhihu collection id in URL")
        collection_id = match.group(1)
        emitted = 0
        current_offset = offset
        seen: set[str] = set()
        while self.max_items is None or emitted < self.max_items:
            page = self._run([
                "zhihu", "collection", collection_id, "--offset", str(current_offset),
                "--limit", "20", "-f", "json",
            ])
            if not isinstance(page, list):
                raise ZhihuClientError("Zhihu browser collection returned an unexpected shape")
            if not page:
                return
            for item in page:
                current_offset += 1
                if not isinstance(item, Mapping):
                    continue
                url = item.get("url")
                if not isinstance(url, str) or url in seen:
                    continue
                seen.add(url)
                yield self._normalize_item(item)
                emitted += 1
                if self.max_items is not None and emitted >= self.max_items:
                    return
            if len(page) < 20:
                return

    def iter_member_articles(self, author_url: str, offset: int = 0) -> Iterable[dict[str, Any]]:
        raise ZhihuClientError("Browser-backed author export is not supported")

    def _normalize_item(self, item: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(item.get("type", ""))
        url = str(item.get("url", ""))
        identity = _identity(kind, url)
        if kind == "answer":
            detail = self._run(["zhihu", "answer-detail", identity, "-f", "json"])
            value = detail[0] if isinstance(detail, list) and detail else detail
            if not isinstance(value, Mapping):
                raise ZhihuClientError("Zhihu browser detail returned an unexpected shape")
            title = str(value.get("question_title") or item.get("title") or "")
            author = str(value.get("author") or item.get("author") or "")
            content = str(value.get("content") or item.get("excerpt") or title)
            created_at = _iso(str(value.get("created_at") or ""))
            url = str(value.get("url") or url)
        else:
            title = str(item.get("title") or "")
            author = str(item.get("author") or "")
            content = str(item.get("excerpt") or title)
            created_at = _now()
        return {
            "schema_version": "1.0",
            "id": f"zhihu_{kind}_{identity}",
            "source": "zhihu",
            "title": title,
            "url": url,
            "author": author,
            "content": content,
            "created_at": created_at,
            "saved_at": _now(),
            "images": [],
        }

    def _run(self, arguments: list[str]) -> dict[str, Any] | list[Any]:
        try:
            return self.gateway.run_json(arguments)
        except OpenCliError as exc:
            raise ZhihuClientError(str(exc)) from None


def _identity(kind: str, url: str) -> str:
    patterns = {
        "answer": r"/answer/(\d+)",
        "article": r"(?:zhuanlan\.zhihu\.com/p/|/p/)(\d+)",
        "pin": r"/pin/(\d+)",
    }
    pattern = patterns.get(kind)
    match = re.search(pattern, url) if pattern else None
    if not match:
        raise ZhihuClientError(f"Unsupported Zhihu browser content type: {kind!r}")
    return match.group(1)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _now()
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
