import json
from pathlib import Path

import pytest

from pkb.douyin.collector import CollectionStopped, FavoritePage, FavoritesCollector


FIXTURE = Path(__file__).parent / "fixtures" / "douyin" / "favorites-page.json"


def raw_item(number: int) -> dict[str, object]:
    return {
        "aweme_id": str(number),
        "share_url": f"https://www.douyin.com/video/{number}",
        "author": {"uid": f"u{number}", "nickname": f"作者{number}"},
        "desc": f"内容{number} #知识",
        "text_extra": [{"hashtag_name": "知识"}],
        "create_time": 1784304000,
    }


def page(numbers, cursor=None) -> FavoritePage:
    return FavoritePage(
        items=tuple(raw_item(number) for number in numbers),
        cursor=cursor,
        observed_at="2026-07-18T00:00:00Z",
    )


class FakeFavoritesBrowser:
    def __init__(self, pages=(), error=None):
        self.pages = list(pages)
        self.error = error
        self.page_calls = 0

    def page(self, cursor):
        self.page_calls += 1
        if self.error is not None:
            raise self.error
        return self.pages.pop(0)


def test_collects_first_twenty_unique_items_and_stops():
    browser = FakeFavoritesBrowser(
        pages=[page(range(1, 16), cursor="page-2"), page(range(10, 31), cursor="page-3")]
    )
    result = FavoritesCollector(browser, delay=lambda _: None).collect(limit=20)
    assert [item.work_id for item in result] == [str(number) for number in range(1, 21)]
    assert browser.page_calls == 2


def test_normalizes_authenticated_browser_fixture():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    browser = FakeFavoritesBrowser(
        pages=[FavoritePage.from_result(payload)]
    )
    [item] = FavoritesCollector(browser, delay=lambda _: None).collect(limit=1)
    assert item.work_id == "7001"
    assert item.author_id == "author-1"
    assert item.author == "知识作者"
    assert item.caption == "一个知识视频 #学习 #AI"
    assert item.hashtags == ("学习", "AI")
    assert item.published_at == "2026-07-17T16:00:00Z"


def test_delays_once_between_pages():
    delays = []
    browser = FakeFavoritesBrowser(
        pages=[page([1], cursor="page-2"), page([2])]
    )
    FavoritesCollector(browser, delay=delays.append, request_delay=7).collect(limit=2)
    assert delays == [7]


def test_collect_all_stops_after_three_no_growth_observations():
    browser = FakeFavoritesBrowser(
        pages=[
            page([1, 2], cursor="2"),
            page([1, 2, 3], cursor="3"),
            page([1, 2, 3]),
            page([1, 2, 3]),
            page([1, 2, 3]),
        ]
    )
    batches = []
    delays = []

    result = FavoritesCollector(
        browser, delay=delays.append, request_delay=7
    ).collect_all(on_discovered=batches.append)

    assert [item.work_id for item in result] == ["1", "2", "3"]
    assert [[item.work_id for item in batch] for batch in batches] == [["1", "2"], ["3"]]
    assert browser.page_calls == 5
    assert delays == [7, 7, 7, 7]


def test_collect_all_deduplicates_ids_already_in_manifest():
    browser = FakeFavoritesBrowser(
        pages=[page([1, 2]), page([1, 2]), page([1, 2]), page([1, 2])]
    )
    batches = []

    result = FavoritesCollector(browser, delay=lambda _: None).collect_all(
        known_ids={"1"}, on_discovered=batches.append
    )

    assert [item.work_id for item in result] == ["2"]
    assert [[item.work_id for item in batch] for batch in batches] == [["2"]]


def test_collect_all_rejects_non_positive_stable_observations():
    with pytest.raises(ValueError, match="stable observations must be positive"):
        FavoritesCollector(FakeFavoritesBrowser()).collect_all(stable_observations=0)


@pytest.mark.parametrize("request_delay", [4.9, 10.1])
def test_rejects_delay_outside_safe_range(request_delay):
    with pytest.raises(ValueError, match="5..10"):
        FavoritesCollector(FakeFavoritesBrowser(), request_delay=request_delay)


@pytest.mark.parametrize("limit", [0, 21])
def test_rejects_limit_outside_trial_boundary(limit):
    with pytest.raises(ValueError, match="1..20"):
        FavoritesCollector(FakeFavoritesBrowser()).collect(limit=limit)


@pytest.mark.parametrize("code", ["auth_required", "captcha", "http_403", "http_429"])
def test_challenge_stops_without_fetching_next_page(code):
    browser = FakeFavoritesBrowser(error=CollectionStopped(code))
    with pytest.raises(CollectionStopped, match=code):
        FavoritesCollector(browser).collect(limit=20)
    assert browser.page_calls == 1


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ({"status": 403}, "http_403"),
        ({"status": 429}, "http_429"),
        ({"error": "login_required"}, "auth_required"),
        ({"error": "captcha_required"}, "captcha"),
    ],
)
def test_structured_browser_stop_results_are_mapped(result, code):
    with pytest.raises(CollectionStopped, match=code):
        FavoritePage.from_result(result)
