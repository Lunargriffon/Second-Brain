"""Bounded normalization of authenticated Douyin favorites pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Callable, Mapping, Protocol, Sequence

from .models import FavoriteItem


_STOP_ERRORS = {
    "login_required": "auth_required",
    "auth_required": "auth_required",
    "captcha_required": "captcha",
    "captcha": "captcha",
}


class CollectionStopped(RuntimeError):
    """A safe, resumable stop requested by the remote browser result."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FavoritePage:
    items: tuple[Mapping[str, object], ...]
    cursor: str | None = None
    observed_at: str = ""

    @classmethod
    def from_result(cls, result: Mapping[str, object]) -> FavoritePage:
        status = result.get("status")
        if status in (403, "403"):
            raise CollectionStopped("http_403")
        if status in (429, "429"):
            raise CollectionStopped("http_429")

        error = str(result.get("error", "")).lower()
        if error in _STOP_ERRORS:
            raise CollectionStopped(_STOP_ERRORS[error])

        raw_items = result.get("items", ())
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raw_items = ()
        items = tuple(item for item in raw_items if isinstance(item, Mapping))
        cursor = result.get("cursor") if result.get("has_more", True) else None
        return cls(
            items=items,
            cursor=None if cursor is None else str(cursor),
            observed_at=str(result.get("observed_at", "")),
        )


class FavoritesBrowser(Protocol):
    def page(self, cursor: str | None) -> FavoritePage: ...


class FavoritesCollector:
    def __init__(
        self,
        browser: FavoritesBrowser,
        *,
        delay: Callable[[float], None] = time.sleep,
        request_delay: float = 7.0,
    ) -> None:
        if not 5 <= request_delay <= 10:
            raise ValueError("request delay must be 5..10 seconds")
        self.browser = browser
        self.delay = delay
        self.request_delay = request_delay

    def collect(self, *, limit: int = 20) -> list[FavoriteItem]:
        if not 1 <= limit <= 20:
            raise ValueError("trial limit must be 1..20")

        collected: list[FavoriteItem] = []
        seen: set[str] = set()
        cursor: str | None = None
        while len(collected) < limit:
            page = self.browser.page(cursor)
            for raw in page.items:
                item = _normalize(raw, observed_at=page.observed_at)
                if item.work_id not in seen:
                    seen.add(item.work_id)
                    collected.append(item)
                    if len(collected) == limit:
                        break
            if len(collected) == limit or page.cursor is None:
                break
            cursor = page.cursor
            self.delay(self.request_delay)
        return collected

    def collect_all(
        self,
        *,
        known_ids: set[str] | None = None,
        on_discovered: Callable[[list[FavoriteItem]], None] | None = None,
        stable_observations: int = 3,
    ) -> list[FavoriteItem]:
        if stable_observations < 1:
            raise ValueError("stable observations must be positive")

        seen = set(known_ids or ())
        discovered: list[FavoriteItem] = []
        cursor: str | None = None
        unchanged = 0
        while unchanged < stable_observations:
            page = self.browser.page(cursor)
            batch: list[FavoriteItem] = []
            for raw in page.items:
                item = _normalize(raw, observed_at=page.observed_at)
                if item.work_id not in seen:
                    seen.add(item.work_id)
                    batch.append(item)

            if batch:
                discovered.extend(batch)
                unchanged = 0
                if on_discovered is not None:
                    on_discovered(batch)
            else:
                unchanged += 1

            if unchanged < stable_observations:
                cursor = page.cursor or str(len(seen))
                self.delay(self.request_delay)

        return discovered


def _normalize(raw: Mapping[str, object], *, observed_at: str) -> FavoriteItem:
    author_value = raw.get("author", {})
    author = author_value if isinstance(author_value, Mapping) else {}
    extras_value = raw.get("text_extra", ())
    extras = extras_value if isinstance(extras_value, Sequence) else ()
    hashtags = tuple(
        str(extra["hashtag_name"])
        for extra in extras
        if isinstance(extra, Mapping) and extra.get("hashtag_name")
    )
    created = raw.get("create_time")
    published_at = None
    if isinstance(created, (int, float)):
        published_at = datetime.fromtimestamp(created, timezone.utc).isoformat().replace("+00:00", "Z")
    return FavoriteItem(
        work_id=str(raw.get("aweme_id", "")),
        url=str(raw.get("share_url", "")),
        author_id=str(author.get("uid", "")),
        author=str(author.get("nickname", "")),
        caption=str(raw.get("desc", "")),
        hashtags=hashtags,
        published_at=published_at,
        observed_at=observed_at,
    )
