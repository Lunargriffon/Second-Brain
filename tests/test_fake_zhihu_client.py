from pathlib import Path

from pkb.zhihu import FakeZhihuClient


def test_fake_zhihu_client_returns_fixture_items_from_offset():
    fixture = Path("tests/fixtures/sample_collection.json")
    client = FakeZhihuClient(fixture)

    articles = list(client.iter_collection("https://www.zhihu.com/collection/1", offset=1))

    assert len(articles) == 1
    assert articles[0]["id"] == "zhihu_789012"
    assert articles[0]["source"] == "zhihu"
    assert articles[0]["schema_version"] == "1.0"
    assert "saved_at" in articles[0]
