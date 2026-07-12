import json
from pathlib import Path

from pkb.sources.x_bookmarks import XBookmarkAdapter
from pkb.sources.zhihu import ZhihuAdapter


FIXTURES = Path("tests/fixtures/knowledge")


def _record(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_zhihu_adapter_uses_platform_identity_and_filename_collection():
    doc = ZhihuAdapter().normalize(
        _record("zhihu-10.jsonl"), raw_path=Path("zhihu-10.jsonl"), raw_line=1
    )

    assert doc.identity_key == "zhihu:answer:123"
    assert doc.membership.collection_id == "10"
    assert doc.plain_content == "先理解，再练习。"


def test_x_adapter_prefers_bookmarked_target_identity_without_redirecting():
    doc = XBookmarkAdapter().normalize(
        _record("x-bookmarks.jsonl"), raw_path=Path("x-bookmarks.jsonl"), raw_line=1
    )

    assert doc.identity_key == "zhihu:answer:123"
    assert doc.canonical_url == "https://www.zhihu.com/question/9/answer/123"
    assert doc.membership.source == "x"
    assert doc.membership.source_url == "https://x.com/a/status/1"


def test_adapters_do_not_stringify_complex_values_as_text():
    zhihu = _record("zhihu-10.jsonl")
    zhihu.update(title=["not", "text"], author={"name": "田"}, content={"x": 1})
    x_record = _record("x-bookmarks.jsonl")
    x_record.update(text=["not", "text"], authorName={"name": "李"})

    zhihu_doc = ZhihuAdapter().normalize(
        zhihu, raw_path=Path("zhihu-10.jsonl"), raw_line=1
    )
    x_doc = XBookmarkAdapter().normalize(
        x_record, raw_path=Path("x-bookmarks.jsonl"), raw_line=1
    )

    assert zhihu_doc.title == ""
    assert zhihu_doc.author == ""
    assert zhihu_doc.plain_content == ""
    assert x_doc.author == ""
    assert x_doc.plain_content == ""


def test_zhihu_collection_requires_an_exact_numeric_archive_filename():
    record = _record("zhihu-10.jsonl")

    author_archive = ZhihuAdapter().normalize(
        record, raw_path=Path("zhihu-author-18868-42.jsonl"), raw_line=1
    )
    sample_archive = ZhihuAdapter().normalize(
        record, raw_path=Path("zhihu-656.sample.jsonl"), raw_line=1
    )

    assert author_archive.membership.collection_id is None
    assert sample_archive.membership.collection_id is None


def test_x_adapter_preserves_only_string_media_urls():
    record = _record("x-bookmarks.jsonl")
    record["media"] = ["https://pbs.twimg.com/a.jpg", {"url": "hidden"}, 42]

    doc = XBookmarkAdapter().normalize(
        record, raw_path=Path("x-bookmarks.jsonl"), raw_line=1
    )

    assert doc.media_urls == ("https://pbs.twimg.com/a.jpg",)


def test_x_adapter_falls_back_to_tweet_id_and_nested_author_name():
    record = _record("x-bookmarks.jsonl")
    record.pop("id")
    record.pop("authorName")
    record["tweetId"] = "tweet-fallback"
    record["author"] = {"name": "Nested Author"}

    doc = XBookmarkAdapter().normalize(
        record, raw_path=Path("x-bookmarks.jsonl"), raw_line=1
    )

    assert doc.membership.source_item_id == "tweet-fallback"
    assert doc.author == "Nested Author"


def test_x_adapter_ignores_quoted_tweet_content_and_identity():
    record = _record("x-bookmarks.jsonl")
    record["quotedTweet"] = {
        "id": "quoted-id",
        "text": "quoted text",
        "links": ["https://www.zhihu.com/question/8/answer/999"],
    }

    doc = XBookmarkAdapter().normalize(
        record, raw_path=Path("x-bookmarks.jsonl"), raw_line=1
    )

    assert doc.identity_key == "zhihu:answer:123"
    assert doc.plain_content == record["text"]
    assert "quoted text" not in doc.plain_content
