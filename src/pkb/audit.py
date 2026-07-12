from __future__ import annotations

import time
from dataclasses import dataclass

from pkb.zhihu import HttpClient, UrllibHttpClient, ZhihuClientError, _collection_id_from_url, _collection_items_url


@dataclass(frozen=True)
class AuditResult:
    collection_id: str
    reported_total: int | None
    scanned_total: int
    type_counts: dict[str, int]
    page_count: int
    warnings: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "collection_id": self.collection_id,
            "reported_total": self.reported_total,
            "scanned_total": self.scanned_total,
            "type_counts": self.type_counts,
            "page_count": self.page_count,
            "warnings": self.warnings,
        }


def audit_zhihu_collection(
    collection_url: str,
    cookie: str,
    http: HttpClient | None = None,
    request_delay: float = 2.0,
) -> AuditResult:
    collection_id = _collection_id_from_url(collection_url)
    http_client = http or UrllibHttpClient()
    headers = _headers(cookie)
    offset = 0
    page_count = 0
    scanned_total = 0
    reported_total: int | None = None
    type_counts: dict[str, int] = {}
    warnings: list[str] = []

    while True:
        payload = http_client.get_json(_collection_items_url(collection_id, offset, 20), headers)
        page_count += 1
        paging = payload.get("paging", {})
        if isinstance(paging, dict) and reported_total is None:
            total = paging.get("totals")
            reported_total = int(total) if total is not None else None

        data = payload.get("data", [])
        if not isinstance(data, list):
            raise ZhihuClientError("Zhihu returned collection audit data in an unexpected shape")
        if not data:
            break

        for item in data:
            content = item.get("content", {}) if isinstance(item, dict) else {}
            content_type = content.get("type") if isinstance(content, dict) else None
            key = str(content_type or "unknown")
            type_counts[key] = type_counts.get(key, 0) + 1
            scanned_total += 1

        offset += len(data)
        if isinstance(paging, dict) and paging.get("is_end") is True:
            break
        if request_delay > 0:
            time.sleep(request_delay)

    if reported_total is not None and reported_total != scanned_total:
        warnings.append("reported_total_mismatch")

    return AuditResult(
        collection_id=collection_id,
        reported_total=reported_total,
        scanned_total=scanned_total,
        type_counts=type_counts,
        page_count=page_count,
        warnings=warnings,
    )


def _headers(cookie: str) -> dict[str, str]:
    if not cookie.strip():
        raise ZhihuClientError("ZHIHU_COOKIE is required for Zhihu audit")
    return {
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
        "Referer": "https://www.zhihu.com/",
        "User-Agent": "Mozilla/5.0",
    }
