import pytest

from pkb.knowledge.urls import canonicalize_url, platform_identity


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "HTTPS://Example.COM:443/a/?utm_source=x&keep=1#part",
            "https://example.com/a?keep=1",
        ),
        (
            "http://Example.COM:80/path?z=2&utm_medium=email&a=1&ref=x",
            "http://example.com/path?a=1&z=2",
        ),
        ("https://example.com:8443/", "https://example.com:8443/"),
        ("https://example.com/a///", "https://example.com/a"),
    ],
)
def test_canonicalize_url_normalizes_structure_and_query(raw, expected):
    assert canonicalize_url(raw) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.zhihu.com/api/v4/answers/123",
        "https://www.zhihu.com/question/9/answer/123",
    ],
)
def test_platform_identity_matches_zhihu_api_and_public_answer_urls(url):
    assert platform_identity(url) == "zhihu:answer:123"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://zhuanlan.zhihu.com/p/456", "zhihu:article:456"),
        ("https://www.zhihu.com/api/v4/articles/456", "zhihu:article:456"),
        ("https://www.zhihu.com/zvideo/789", "zhihu:zvideo:789"),
        ("https://www.zhihu.com/api/v4/zvideos/789", "zhihu:zvideo:789"),
    ],
)
def test_platform_identity_recognizes_articles_and_zvideos(url, expected):
    assert platform_identity(url) == expected


def test_platform_identity_returns_none_for_unknown_platform():
    assert platform_identity("https://example.com/question/9/answer/123") is None
