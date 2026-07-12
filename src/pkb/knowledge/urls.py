"""Shared URL canonicalization and platform identity extraction."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "source",
    "spm",
    "ref",
    "ref_src",
}


def canonicalize_url(value: str) -> str:
    """Return a stable URL with tracking data and fragments removed."""
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()

    port = parts.port
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        host = f"{host}:{port}"

    path = parts.path.rstrip("/") or "/"
    query_pairs = [
        pair
        for pair in parse_qsl(parts.query, keep_blank_values=True)
        if pair[0].lower() not in TRACKING_KEYS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, host, path, query, ""))


_ZHIHU_PATTERNS = (
    (re.compile(r"^/api/v4/answers/(\d+)(?:/|$)"), "answer"),
    (re.compile(r"^/question/\d+/answer/(\d+)(?:/|$)"), "answer"),
    (re.compile(r"^/api/v4/articles/(\d+)(?:/|$)"), "article"),
    (re.compile(r"^/p/(\d+)(?:/|$)"), "article"),
    (re.compile(r"^/api/v4/zvideos/(\d+)(?:/|$)"), "zvideo"),
    (re.compile(r"^/zvideo/(\d+)(?:/|$)"), "zvideo"),
)


def platform_identity(value: str) -> str | None:
    """Extract a stable identity from a recognized platform URL."""
    parts = urlsplit(value)
    if (parts.hostname or "").lower() not in {
        "zhihu.com",
        "www.zhihu.com",
        "zhuanlan.zhihu.com",
    }:
        return None

    for pattern, kind in _ZHIHU_PATTERNS:
        if match := pattern.match(parts.path):
            return f"zhihu:{kind}:{match.group(1)}"
    return None
