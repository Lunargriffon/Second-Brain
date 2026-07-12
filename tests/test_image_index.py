import json

from pkb.cli import main
from pkb.image_index import build_zhihu_image_index, write_zhihu_image_index


def test_builds_article_image_index_and_missing_candidates(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    report = tmp_path / "triage.json"
    manifest.write_text(
        json.dumps(
            {
                "article_id": "zhihu_answer_1",
                "article_title": "代码截图怎么理解？",
                "article_url": "https://www.zhihu.com/question/1/answer/1",
                "bucket": "keep_knowledge",
                "image_url": "https://example.test/one.jpg",
                "path": "data/images/one.jpg",
                "reasons": ["knowledge_context"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    report.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "article_id": "zhihu_answer_1",
                        "article_title": "代码截图怎么理解？",
                        "article_url": "https://www.zhihu.com/question/1/answer/1",
                        "bucket": "keep_knowledge",
                        "image_url": "https://example.test/one.jpg",
                    },
                    {
                        "article_id": "zhihu_answer_2",
                        "article_title": "软件推荐",
                        "article_url": "https://www.zhihu.com/question/2/answer/2",
                        "bucket": "keep_knowledge",
                        "image_url": "https://example.test/missing.jpg",
                    },
                    {
                        "article_id": "zhihu_answer_3",
                        "bucket": "reject_decorative",
                        "image_url": "https://example.test/wallpaper.jpg",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    index = build_zhihu_image_index(manifest_path=manifest, triage_report_path=report)

    assert index["summary"]["articles"] == 1
    assert index["summary"]["images"] == 1
    assert index["summary"]["missing_candidates"] == 1
    assert index["articles"][0]["article_id"] == "zhihu_answer_1"
    assert index["articles"][0]["images"][0]["path"] == "data/images/one.jpg"
    assert index["missing_candidates"][0]["article_id"] == "zhihu_answer_2"


def test_write_zhihu_image_index_outputs_json(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    report = tmp_path / "triage.json"
    output = tmp_path / "index.json"
    manifest.write_text("", encoding="utf-8")
    report.write_text(json.dumps({"items": []}), encoding="utf-8")

    result = write_zhihu_image_index(manifest_path=manifest, triage_report_path=report, output_path=output)

    assert result["summary"]["images"] == 0
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["images"] == 0


def test_cli_writes_zhihu_image_index(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    report = tmp_path / "triage.json"
    output = tmp_path / "index.json"
    manifest.write_text("", encoding="utf-8")
    report.write_text(json.dumps({"items": []}), encoding="utf-8")

    exit_code = main(
        [
            "images",
            "zhihu-index",
            "--manifest",
            str(manifest),
            "--report",
            str(report),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.exists()
