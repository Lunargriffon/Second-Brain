from __future__ import annotations

import json
import re
import time
import http.client
import urllib.error
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Protocol
from urllib.parse import urlencode


class ZhihuClient(Protocol):
    def iter_collection(self, collection_url: str, offset: int = 0) -> Iterable[dict[str, Any]]:
        """Yield normalized article dictionaries from a Zhihu collection."""

    def iter_member_articles(self, author_url: str, offset: int = 0) -> Iterable[dict[str, Any]]:
        """Yield normalized article dictionaries from a Zhihu member page."""


class ZhihuClientError(RuntimeError):
    """Raised when the Zhihu adapter cannot export reliably."""


class HttpClient(Protocol):
    def get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        """Return a JSON object or raise ZhihuClientError."""


class UrllibHttpClient:
    def __init__(self, timeout: float = 45.0) -> None:
        self.timeout = timeout

    def get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 429}:
                raise ZhihuClientError(f"Zhihu stopped export with HTTP {exc.code}") from exc
            raise ZhihuClientError(f"Zhihu request failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ZhihuClientError(f"Zhihu request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ZhihuClientError("Zhihu request timed out") from exc
        except http.client.RemoteDisconnected as exc:
            raise ZhihuClientError("Zhihu closed the connection") from exc
        except http.client.IncompleteRead as exc:
            raise ZhihuClientError("Zhihu response was incomplete") from exc

        if "json" not in content_type.lower():
            raise ZhihuClientError("Zhihu returned non-JSON response; stop to avoid unsafe retry loops")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ZhihuClientError("Zhihu returned invalid JSON response") from exc
        if not isinstance(payload, dict):
            raise ZhihuClientError("Zhihu returned unexpected JSON response")
        return payload


class FakeZhihuClient:
    def __init__(self, fixture_path: Path) -> None:
        self.fixture_path = fixture_path

    def iter_collection(self, collection_url: str, offset: int = 0) -> Iterable[dict[str, Any]]:
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        for item in payload["items"][offset:]:
            yield _normalize_fixture_item(item)


class RealZhihuClient:
    def __init__(
        self,
        cookie: str,
        request_delay: float = 2.0,
        max_retry: int = 3,
        max_items: int | None = 5,
        http: HttpClient | None = None,
    ) -> None:
        self.cookie = cookie
        self.request_delay = request_delay
        self.max_retry = max_retry
        self.max_items = max_items
        self.http = http or UrllibHttpClient()

    def iter_collection(self, collection_url: str, offset: int = 0) -> Iterable[dict[str, Any]]:
        collection_id = _collection_id_from_url(collection_url)
        headers = self._headers()
        exported = 0
        current_offset = offset

        while True:
            if self.max_items is not None and exported >= self.max_items:
                return

            url = _collection_items_url(collection_id, current_offset, 20)
            payload = self._get_json_with_retry(url, headers)
            items = payload.get("data", [])
            if not isinstance(items, list):
                raise ZhihuClientError("Zhihu returned collection data in an unexpected shape")

            if not items:
                return

            for item in items:
                if self.max_items is not None and exported >= self.max_items:
                    return
                yield self._article_from_collection_item(item, headers)
                exported += 1
                current_offset += 1
                if self.request_delay > 0:
                    time.sleep(self.request_delay)

            paging = payload.get("paging", {})
            if isinstance(paging, dict) and paging.get("is_end") is True:
                return

    def iter_member_articles(self, author_url: str, offset: int = 0) -> Iterable[dict[str, Any]]:
        url_token = _member_url_token_from_url(author_url)
        headers = self._headers()
        exported = 0
        current_offset = offset

        while True:
            if self.max_items is not None and exported >= self.max_items:
                return

            url = _member_articles_url(url_token, current_offset, 20)
            payload = self._get_json_with_retry(url, headers)
            items = payload.get("data", [])
            if not isinstance(items, list):
                raise ZhihuClientError("Zhihu returned member article data in an unexpected shape")

            if not items:
                return

            for item in items:
                if self.max_items is not None and exported >= self.max_items:
                    return
                if item.get("type") != "article":
                    raise ZhihuClientError(f"Unsupported Zhihu member content type: {item.get('type')!r}")
                article_id = item.get("id")
                if article_id is None:
                    raise ZhihuClientError("Zhihu member article is missing id")
                if item.get("content"):
                    yield _normalize_article(item)
                else:
                    detail = self._get_json_with_retry(_detail_url("article", str(article_id)), headers)
                    yield _normalize_article(detail)
                exported += 1
                current_offset += 1
                if self.request_delay > 0:
                    time.sleep(self.request_delay)

            paging = payload.get("paging", {})
            if isinstance(paging, dict) and paging.get("is_end") is True:
                return

    def _headers(self) -> dict[str, str]:
        if not self.cookie.strip():
            raise ZhihuClientError("ZHIHU_COOKIE is required for real Zhihu export")
        return {
            "Accept": "application/json, text/plain, */*",
            "Cookie": self.cookie,
            "Referer": "https://www.zhihu.com/",
            "User-Agent": "Mozilla/5.0",
        }

    def _article_from_collection_item(self, item: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        content = item.get("content")
        if not isinstance(content, dict):
            raise ZhihuClientError("Zhihu collection item is missing content")

        content_type = content.get("type")
        content_id = content.get("id")
        if content_type == "pin" and content_id is not None:
            return _normalize_pin(content)
        if content_type == "zvideo" and content_id is not None:
            return _normalize_zvideo(content)

        if content_type not in {"answer", "article"} or content_id is None:
            raise ZhihuClientError(f"Unsupported Zhihu collection content type: {content_type!r}")

        if content_type == "article" and content.get("content"):
            return _normalize_article(content)

        detail = self._get_json_with_retry(_detail_url(str(content_type), str(content_id)), headers)
        if content_type == "answer":
            return _normalize_answer(detail)
        return _normalize_article(detail)

    def _get_json_with_retry(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        attempts = max(1, self.max_retry)
        for attempt in range(1, attempts + 1):
            try:
                return self.http.get_json(url, headers)
            except ZhihuClientError as exc:
                message = str(exc)
                if "HTTP 401" in message or "HTTP 403" in message or "HTTP 429" in message:
                    raise
                if attempt == attempts:
                    raise
                if self.request_delay > 0:
                    time.sleep(self.request_delay)
        raise ZhihuClientError("Zhihu request failed")


def _normalize_fixture_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "id": f"zhihu_{item['id']}",
        "source": "zhihu",
        "title": item["title"],
        "url": item["url"],
        "author": item["author"],
        "content": item["content"],
        "created_at": item["created_at"],
        "saved_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "images": item.get("images", []),
    }


def _collection_id_from_url(collection_url: str) -> str:
    match = re.search(r"/collection/(\d+)", collection_url)
    if not match:
        raise ZhihuClientError("Could not find Zhihu collection id in URL")
    return match.group(1)


def _member_url_token_from_url(author_url: str) -> str:
    match = re.search(r"/people/([^/?#]+)", author_url)
    if not match:
        raise ZhihuClientError("Could not find Zhihu member url token in URL")
    return match.group(1)


def _collection_items_url(collection_id: str, offset: int, limit: int) -> str:
    query = urlencode(
        {
            "offset": offset,
            "limit": limit,
        }
    )
    return f"https://www.zhihu.com/api/v4/collections/{collection_id}/items?{query}"


def _member_articles_url(url_token: str, offset: int, limit: int) -> str:
    query = urlencode(
        {
            "offset": offset,
            "limit": limit,
            "sort_by": "created",
        }
    )
    return f"https://www.zhihu.com/api/v4/members/{url_token}/articles?{query}"


def _detail_url(content_type: str, content_id: str) -> str:
    if content_type == "answer":
        query = urlencode({"include": "content,created_time,question,author"})
        return f"https://www.zhihu.com/api/v4/answers/{content_id}?{query}"
    return f"https://zhuanlan.zhihu.com/api/articles/{content_id}"
def _normalize_answer(detail: dict[str, Any]) -> dict[str, Any]:
    answer_id = _required(detail, "id")
    question = detail.get("question")
    if not isinstance(question, dict):
        raise ZhihuClientError("Zhihu answer is missing question metadata")
    title = str(_required(question, "title"))
    content_html = _optional_html(detail.get("content"))
    content_text = _html_to_text(content_html) if content_html else title
    return {
        "schema_version": "1.0",
        "id": f"zhihu_answer_{answer_id}",
        "source": "zhihu",
        "title": title,
        "url": str(_required(detail, "url")),
        "author": _author_name(detail),
        "content": content_text,
        "created_at": _unix_to_iso(_required(detail, "created_time")),
        "saved_at": _now_iso(),
        "images": _html_image_urls(content_html) if content_html else [],
    }


def _normalize_article(detail: dict[str, Any]) -> dict[str, Any]:
    article_id = _required(detail, "id")
    title = str(_required(detail, "title"))
    content_html = _optional_html(detail.get("content"))
    content_text = _html_to_text(content_html) if content_html else title
    return {
        "schema_version": "1.0",
        "id": f"zhihu_article_{article_id}",
        "source": "zhihu",
        "title": title,
        "url": str(_required(detail, "url")),
        "author": _author_name(detail),
        "content": content_text,
        "created_at": _unix_to_iso(_required(detail, "created")),
        "saved_at": _now_iso(),
        "images": _html_image_urls(content_html) if content_html else [],
    }


def _normalize_pin(detail: dict[str, Any]) -> dict[str, Any]:
    pin_id = _required(detail, "id")
    content_html = _pin_content_html(detail)
    content_text = _html_to_text(content_html)
    return {
        "schema_version": "1.0",
        "id": f"zhihu_pin_{pin_id}",
        "source": "zhihu",
        "title": content_text[:80] or f"Zhihu pin {pin_id}",
        "url": str(_required(detail, "url")),
        "author": _author_name(detail),
        "content": content_text,
        "created_at": _unix_to_iso(_required(detail, "created")),
        "saved_at": _now_iso(),
        "images": _pin_image_urls(detail),
    }


def _normalize_zvideo(detail: dict[str, Any]) -> dict[str, Any]:
    video_id = _required(detail, "id")
    title = str(_required(detail, "title"))
    created = detail.get("created_at", detail.get("created"))
    return {
        "schema_version": "1.0",
        "id": f"zhihu_zvideo_{video_id}",
        "source": "zhihu",
        "title": title,
        "url": str(_required(detail, "url")),
        "author": _author_name(detail),
        "content": title,
        "created_at": _unix_to_iso(created),
        "saved_at": _now_iso(),
        "images": _zvideo_image_urls(detail),
    }


def _pin_content_html(detail: dict[str, Any]) -> str:
    blocks = detail.get("content")
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("own_text") or block.get("content") or ""
            if isinstance(text, str):
                parts.append(text)
    return "<br>".join(parts)


def _pin_image_urls(detail: dict[str, Any]) -> list[str]:
    blocks = detail.get("content")
    if not isinstance(blocks, list):
        return []
    urls: list[str] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        for key in ("original_url", "url", "watermark_url"):
            value = block.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in urls:
                urls.append(value)
                break
    return urls


def _zvideo_image_urls(detail: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("thumbnail", "image_url", "cover_url"):
        value = detail.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in urls:
            urls.append(value)
    thumbnail = detail.get("thumbnail_info")
    if isinstance(thumbnail, dict):
        value = thumbnail.get("url")
        if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in urls:
            urls.append(value)
    return urls


def _required(payload: dict[str, Any], field: str) -> Any:
    value = payload.get(field)
    if value in {None, ""}:
        raise ZhihuClientError(f"Zhihu response is missing required field: {field}")
    return value


def _optional_html(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return value


def _author_name(payload: dict[str, Any]) -> str:
    author = payload.get("author")
    if not isinstance(author, dict):
        raise ZhihuClientError("Zhihu response is missing author metadata")
    return str(_required(author, "name"))


def _unix_to_iso(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError) as exc:
        raise ZhihuClientError("Zhihu response has invalid timestamp") from exc
    return datetime.fromtimestamp(timestamp, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return re.sub(r"\s+", " ", parser.text).strip()


def _html_image_urls(value: str) -> list[str]:
    parser = _ImageExtractor()
    parser.feed(value)
    return parser.urls


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        return " ".join(self._parts)

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._parts.append(data.strip())


class _ImageExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        attrs_dict = dict(attrs)
        src = attrs_dict.get("src") or attrs_dict.get("data-original") or attrs_dict.get("data-actualsrc")
        if src and src.startswith(("http://", "https://")) and src not in self.urls:
            self.urls.append(src)
