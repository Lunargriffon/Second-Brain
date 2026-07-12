import json

from pkb.cli import main
from pkb.image_triage import ImageTriageBucket, build_image_triage_report, classify_image_record


def test_classifies_diagram_table_code_images_as_keep():
    record = {
        "id": "zhihu_answer_1",
        "title": "如何阅读论文并整理流程图？",
        "content": "下面这张图表和代码截图是核心方法。",
        "url": "https://www.zhihu.com/question/1/answer/1",
        "images": ["https://example.test/chart.png"],
    }

    items = classify_image_record(record, collection_id="575638886")

    assert len(items) == 1
    assert items[0].bucket == ImageTriageBucket.KEEP_KNOWLEDGE
    assert "knowledge_context" in items[0].reasons


def test_classifies_obvious_wallpaper_avatar_or_portrait_as_reject():
    record = {
        "id": "zhihu_answer_2",
        "title": "有哪些适合当头像的壁纸？",
        "content": "分享一些好看的头像和壁纸。",
        "url": "https://www.zhihu.com/question/2/answer/2",
        "images": ["https://example.test/avatar.jpg"],
    }

    items = classify_image_record(record, collection_id="575638886")

    assert items[0].bucket == ImageTriageBucket.REJECT_DECORATIVE
    assert "decorative_context" in items[0].reasons


def test_title_level_decorative_context_overrides_generic_image_mentions():
    record = {
        "id": "zhihu_answer_4",
        "title": "在校大学生如何穿搭？",
        "content": "下面有很多图片示例和购物清单。",
        "url": "https://www.zhihu.com/question/4/answer/4",
        "images": ["https://example.test/outfit.jpg"],
    }

    items = classify_image_record(record, collection_id="575638886")

    assert items[0].bucket == ImageTriageBucket.REJECT_DECORATIVE


def test_title_level_game_context_overrides_generic_screenshot_mentions():
    record = {
        "id": "zhihu_answer_5",
        "title": "Steam 上有哪些必买游戏？",
        "content": "这里有游戏截图。",
        "url": "https://www.zhihu.com/question/5/answer/5",
        "images": ["https://example.test/game.jpg"],
    }

    items = classify_image_record(record, collection_id="575638886")

    assert items[0].bucket == ImageTriageBucket.REJECT_GAME_ENTERTAINMENT


def test_classifies_unknown_images_as_review_instead_of_rejecting():
    record = {
        "id": "zhihu_answer_3",
        "title": "有哪些让你印象很深的经历？",
        "content": "这里有一些经历和细节。",
        "url": "https://www.zhihu.com/question/3/answer/3",
        "images": ["https://example.test/unknown.webp"],
    }

    items = classify_image_record(record, collection_id="575638886")

    assert items[0].bucket == ImageTriageBucket.REVIEW_UNKNOWN


def test_build_image_triage_report_counts_buckets_and_articles(tmp_path):
    raw = tmp_path / "zhihu-575638886.jsonl"
    raw.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "zhihu_answer_1",
                        "title": "代码截图怎么理解？",
                        "content": "这张截图是关键。",
                        "url": "https://www.zhihu.com/question/1/answer/1",
                        "images": ["https://example.test/one.jpg", "https://example.test/two.jpg"],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "id": "zhihu_answer_2",
                        "title": "头像壁纸分享",
                        "content": "",
                        "url": "https://www.zhihu.com/question/2/answer/2",
                        "images": ["https://example.test/avatar.jpg"],
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_image_triage_report([raw])

    assert report["summary"]["images_total"] == 3
    assert report["summary"]["articles_with_images"] == 2
    assert report["buckets"]["keep_knowledge"]["images"] == 2
    assert report["buckets"]["reject_decorative"]["images"] == 1


def test_cli_writes_zhihu_image_triage_report(tmp_path):
    raw_dir = tmp_path / "raw"
    report_path = tmp_path / "state" / "triage.json"
    raw_dir.mkdir()
    (raw_dir / "zhihu-575638886.jsonl").write_text(
        json.dumps(
            {
                "id": "zhihu_answer_1",
                "title": "如何看懂这张图表？",
                "content": "图表是核心信息。",
                "url": "https://www.zhihu.com/question/1/answer/1",
                "images": ["https://example.test/chart.png"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(["images", "zhihu-triage", "--raw-dir", str(raw_dir), "--report", str(report_path)])

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["images_total"] == 1
    assert report["buckets"]["keep_knowledge"]["images"] == 1
