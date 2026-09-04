from pkb.zhihu_opencli import OpenCliZhihuClient


class Gateway:
    def __init__(self, pages, details=None):
        self.pages = list(pages)
        self.details = details or {}
        self.calls = []

    def run_json(self, arguments):
        self.calls.append(arguments)
        if arguments[:2] == ["zhihu", "collection"]:
            return self.pages.pop(0)
        return self.details[arguments[2]]


def test_browser_client_pages_and_normalizes_answer_detail():
    page = [{
        "type": "answer", "title": "问题", "author": "作者", "excerpt": "摘要",
        "url": "https://www.zhihu.com/question/1/answer/2",
    }]
    detail = [{
        "id": "2", "author": "作者", "question_title": "问题",
        "url": "https://www.zhihu.com/question/1/answer/2",
        "created_at": "2026-01-02T03:04:05.000Z", "content": "完整正文",
    }]
    gateway = Gateway([page], {"2": detail})

    records = list(OpenCliZhihuClient(gateway=gateway, max_items=1).iter_collection(
        "https://www.zhihu.com/collection/10"
    ))

    assert records[0]["id"] == "zhihu_answer_2"
    assert records[0]["content"] == "完整正文"
    assert records[0]["created_at"] == "2026-01-02T03:04:05Z"
    assert gateway.calls[0] == [
        "zhihu", "collection", "10", "--offset", "0", "--limit", "20", "-f", "json"
    ]


def test_browser_client_uses_summary_for_article_and_pin():
    page = [
        {"type": "article", "title": "文章", "author": "甲", "excerpt": "文章摘要",
         "url": "https://zhuanlan.zhihu.com/p/3"},
        {"type": "pin", "title": "想法", "author": "乙", "excerpt": "想法正文",
         "url": "https://www.zhihu.com/pin/4"},
    ]

    records = list(OpenCliZhihuClient(gateway=Gateway([page]), max_items=2).iter_collection(
        "https://www.zhihu.com/collection/10"
    ))

    assert [record["id"] for record in records] == ["zhihu_article_3", "zhihu_pin_4"]
    assert [record["content"] for record in records] == ["文章摘要", "想法正文"]
    assert all(record["created_at"].endswith("Z") for record in records)


def test_browser_client_stops_after_short_page_and_deduplicates_urls():
    row = {"type": "pin", "title": "想法", "author": "乙", "excerpt": "正文",
           "url": "https://www.zhihu.com/pin/4"}
    records = list(OpenCliZhihuClient(gateway=Gateway([[row, row]])).iter_collection(
        "https://www.zhihu.com/collection/10"
    ))
    assert len(records) == 1
