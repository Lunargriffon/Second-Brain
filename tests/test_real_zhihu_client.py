
import pytest

from pkb.zhihu import RealZhihuClient, ZhihuClientError


class FakeHttp:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(self, url: str, headers: dict[str, str]) -> dict:
        self.urls.append(url)
        if "/collections/1/items" in url:
            return {
                "data": [
                    {
                        "created": 1710000000,
                        "content": {
                            "type": "answer",
                            "id": 456,
                            "url": "https://www.zhihu.com/question/123/answer/456",
                            "question": {"title": "A useful answer"},
                            "author": {"name": "Example Author"},
                        },
                    }
                ],
                "paging": {"is_end": True},
            }
        if "/answers/456" in url:
            return {
                "id": 456,
                "url": "https://www.zhihu.com/question/123/answer/456",
                "content": "<p>Useful content.</p>",
                "created_time": 1705291200,
                "question": {"title": "A useful answer"},
                "author": {"name": "Example Author"},
            }
        raise AssertionError(f"Unexpected URL: {url}")


def test_real_zhihu_client_fetches_collection_items_and_full_answer_content():
    http = FakeHttp()
    client = RealZhihuClient(cookie="cookie", max_items=1, request_delay=0, http=http)

    articles = list(client.iter_collection("https://www.zhihu.com/collection/1"))

    assert articles[0]["id"] == "zhihu_answer_456"
    assert articles[0]["title"] == "A useful answer"
    assert articles[0]["content"] == "Useful content."
    assert articles[0]["created_at"] == "2024-01-15T04:00:00Z"
    assert len(http.urls) == 2


def test_real_zhihu_client_uses_stable_page_size_and_truncates_locally():
    class ManyItemsHttp:
        def __init__(self) -> None:
            self.collection_urls: list[str] = []

        def get_json(self, url: str, headers: dict[str, str]) -> dict:
            if "/collections/1/items" in url:
                self.collection_urls.append(url)
                return {
                    "data": [
                        {
                            "content": {
                                "type": "answer",
                                "id": i,
                                "url": f"https://www.zhihu.com/question/123/answer/{i}",
                                "question": {"title": f"Title {i}"},
                                "author": {"name": "Author"},
                            }
                        }
                        for i in range(10, 20)
                    ],
                    "paging": {"is_end": False},
                }
            answer_id = url.split("/answers/", 1)[1].split("?", 1)[0]
            return {
                "id": answer_id,
                "url": f"https://www.zhihu.com/question/123/answer/{answer_id}",
                "content": "<p>Content</p>",
                "created_time": 1705291200,
                "question": {"title": f"Title {answer_id}"},
                "author": {"name": "Author"},
            }

    http = ManyItemsHttp()
    client = RealZhihuClient(cookie="cookie", max_items=5, request_delay=0, http=http)

    articles = list(client.iter_collection("https://www.zhihu.com/collection/1"))

    assert len(articles) == 5
    assert "limit=20" in http.collection_urls[0]


def test_real_zhihu_client_extracts_image_urls_without_downloading():
    class ImageHttp:
        def get_json(self, url: str, headers: dict[str, str]) -> dict:
            if "/collections/1/items" in url:
                return {
                    "data": [
                        {
                            "content": {
                                "type": "answer",
                                "id": 456,
                                "url": "https://www.zhihu.com/question/123/answer/456",
                                "question": {"title": "A useful answer"},
                                "author": {"name": "Example Author"},
                            }
                        }
                    ],
                    "paging": {"is_end": True},
                }
            return {
                "id": 456,
                "url": "https://www.zhihu.com/question/123/answer/456",
                "content": '<p>Text</p><img src="https://pic.zhimg.com/example.jpg">',
                "created_time": 1705291200,
                "question": {"title": "A useful answer"},
                "author": {"name": "Example Author"},
            }

    client = RealZhihuClient(cookie="cookie", max_items=1, request_delay=0, http=ImageHttp())

    articles = list(client.iter_collection("https://www.zhihu.com/collection/1"))

    assert articles[0]["images"] == ["https://pic.zhimg.com/example.jpg"]


def test_real_zhihu_client_uses_collection_payload_for_article_content():
    class ArticleHttp:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get_json(self, url: str, headers: dict[str, str]) -> dict:
            self.urls.append(url)
            return {
                "data": [
                    {
                        "content": {
                            "type": "article",
                            "id": 2041120729622729028,
                            "title": "Article Title",
                            "url": "https://zhuanlan.zhihu.com/p/2041120729622729028",
                            "content": "<p>Article content from collection payload.</p>",
                            "created": 1705291200,
                            "author": {"name": "Article Author"},
                        }
                    }
                ],
                "paging": {"is_end": True},
            }

    http = ArticleHttp()
    client = RealZhihuClient(cookie="cookie", max_items=1, request_delay=0, http=http)

    articles = list(client.iter_collection("https://www.zhihu.com/collection/1"))

    assert articles[0]["id"] == "zhihu_article_2041120729622729028"
    assert articles[0]["content"] == "Article content from collection payload."
    assert len(http.urls) == 1


def test_real_zhihu_client_fetches_member_articles_with_full_content():
    class MemberArticleHttp:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get_json(self, url: str, headers: dict[str, str]) -> dict:
            self.urls.append(url)
            if "/members/18868-42/articles" in url:
                return {
                    "data": [
                        {
                            "type": "article",
                            "id": 2052841918812394868,
                            "title": "普通人最重要的是控制回撤",
                            "url": "https://zhuanlan.zhihu.com/p/2052841918812394868",
                            "created": 1705291200,
                            "author": {"name": "Example Author"},
                        }
                    ],
                    "paging": {"is_end": True},
                }
            if "/articles/2052841918812394868" in url:
                return {
                    "id": 2052841918812394868,
                    "title": "普通人最重要的是控制回撤",
                    "url": "https://zhuanlan.zhihu.com/p/2052841918812394868",
                    "content": '<p>Full article.</p><img src="https://pic.zhimg.com/a.jpg">',
                    "created": 1705291200,
                    "author": {"name": "Example Author"},
                }
            raise AssertionError(f"Unexpected URL: {url}")

    http = MemberArticleHttp()
    client = RealZhihuClient(cookie="cookie", max_items=1, request_delay=0, http=http)

    articles = list(client.iter_member_articles("https://www.zhihu.com/people/18868-42"))

    assert articles[0]["id"] == "zhihu_article_2052841918812394868"
    assert articles[0]["content"] == "Full article."
    assert articles[0]["images"] == ["https://pic.zhimg.com/a.jpg"]
    assert "offset=0" in http.urls[0]
    assert len(http.urls) == 2


def test_real_zhihu_client_normalizes_pin_from_collection_payload():
    class PinHttp:
        def get_json(self, url: str, headers: dict[str, str]) -> dict:
            return {
                "data": [
                    {
                        "content": {
                            "type": "pin",
                            "id": 2033287985400169306,
                            "url": "https://www.zhihu.com/pin/2033287985400169306",
                            "created": 1705291200,
                            "author": {"name": "Pin Author"},
                            "content": [
                                {"type": "text", "own_text": "Pin text<br>with link"},
                                {"type": "image", "original_url": "https://pic.zhimg.com/pin.png"},
                            ],
                        }
                    }
                ],
                "paging": {"is_end": True},
            }

    client = RealZhihuClient(cookie="cookie", max_items=1, request_delay=0, http=PinHttp())

    articles = list(client.iter_collection("https://www.zhihu.com/collection/1"))

    assert articles[0]["id"] == "zhihu_pin_2033287985400169306"
    assert articles[0]["title"] == "Pin text with link"
    assert articles[0]["content"] == "Pin text with link"
    assert articles[0]["images"] == ["https://pic.zhimg.com/pin.png"]


def test_real_zhihu_client_normalizes_zvideo_metadata_from_collection_payload():
    class ZvideoHttp:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get_json(self, url: str, headers: dict[str, str]) -> dict:
            self.urls.append(url)
            return {
                "data": [
                    {
                        "content": {
                            "type": "zvideo",
                            "id": 987,
                            "title": "Video Title",
                            "url": "https://www.zhihu.com/zvideo/987",
                            "created_at": 1705291200,
                            "author": {"name": "Video Author"},
                            "thumbnail": "https://pic.zhimg.com/video.jpg",
                        }
                    }
                ],
                "paging": {"is_end": True},
            }

    http = ZvideoHttp()
    client = RealZhihuClient(cookie="cookie", max_items=1, request_delay=0, http=http)

    articles = list(client.iter_collection("https://www.zhihu.com/collection/1"))

    assert articles[0]["id"] == "zhihu_zvideo_987"
    assert articles[0]["title"] == "Video Title"
    assert articles[0]["content"] == "Video Title"
    assert articles[0]["images"] == ["https://pic.zhimg.com/video.jpg"]
    assert len(http.urls) == 1


def test_real_zhihu_client_keeps_answer_metadata_when_detail_has_no_content():
    class MissingContentHttp:
        def get_json(self, url: str, headers: dict[str, str]) -> dict:
            if "/collections/1/items" in url:
                return {
                    "data": [
                        {
                            "content": {
                                "type": "answer",
                                "id": 456,
                                "url": "https://www.zhihu.com/question/123/answer/456",
                                "question": {"title": "Unavailable answer"},
                                "author": {"name": "Example Author"},
                            }
                        }
                    ],
                    "paging": {"is_end": True},
                }
            return {
                "id": 456,
                "url": "https://www.zhihu.com/question/123/answer/456",
                "created_time": 1705291200,
                "question": {"title": "Unavailable answer"},
                "author": {"name": "Example Author"},
            }

    client = RealZhihuClient(cookie="cookie", max_items=1, request_delay=0, http=MissingContentHttp())

    articles = list(client.iter_collection("https://www.zhihu.com/collection/1"))

    assert articles[0]["id"] == "zhihu_answer_456"
    assert articles[0]["title"] == "Unavailable answer"
    assert articles[0]["content"] == "Unavailable answer"
    assert articles[0]["images"] == []


def test_real_zhihu_client_requires_a_collection_id_url():
    client = RealZhihuClient(cookie="cookie", request_delay=0, http=FakeHttp())

    with pytest.raises(ZhihuClientError, match="collection id"):
        list(client.iter_collection("https://www.zhihu.com/"))


def test_real_zhihu_client_stops_on_non_json_response():
    class NonJsonHttp:
        def get_json(self, url: str, headers: dict[str, str]) -> dict:
            raise ZhihuClientError("Zhihu returned non-JSON response")

    client = RealZhihuClient(cookie="cookie", request_delay=0, http=NonJsonHttp())

    with pytest.raises(ZhihuClientError, match="non-JSON"):
        list(client.iter_collection("https://www.zhihu.com/collection/1"))
