import json
from http.client import IncompleteRead

from pkb.checkpoint import CheckpointStore
from pkb.cli import main
from pkb.images import ImageDownloadResult, download_zhihu_images, download_zhihu_triage_images


def test_image_downloader_uses_separate_checkpoint(tmp_path):
    raw = tmp_path / "raw" / "zhihu-575638886.jsonl"
    output_dir = tmp_path / "images" / "zhihu-575638886"
    state = tmp_path / "state" / "images-575638886.state.json"
    raw.parent.mkdir()
    raw.write_text(
        json.dumps({"id": "a1", "images": ["https://example.test/one.jpg"]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    result = download_zhihu_images(
        raw_path=raw,
        output_dir=output_dir,
        checkpoint_store=CheckpointStore(state),
        fetch_image=lambda url: (200, b"image-bytes", "image/jpeg"),
        limit=20,
        request_delay=0,
    )

    assert result == ImageDownloadResult(downloaded=1, skipped=0, stopped_reason=None)
    assert len(list(output_dir.iterdir())) == 1
    assert json.loads(state.read_text(encoding="utf-8"))["offset"] == 1


def test_image_downloader_resumes_from_checkpoint(tmp_path):
    raw = tmp_path / "raw" / "zhihu-575638886.jsonl"
    output_dir = tmp_path / "images" / "zhihu-575638886"
    state = tmp_path / "state" / "images-575638886.state.json"
    raw.parent.mkdir()
    raw.write_text(
        "\n".join(
            [
                json.dumps({"id": "a1", "images": ["https://example.test/one.jpg"]}, ensure_ascii=False),
                json.dumps({"id": "a2", "images": ["https://example.test/two.jpg"]}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    CheckpointStore(state).save(offset=1, exported=1)
    seen_urls = []

    result = download_zhihu_images(
        raw_path=raw,
        output_dir=output_dir,
        checkpoint_store=CheckpointStore(state),
        fetch_image=lambda url: seen_urls.append(url) or (200, b"image-bytes", "image/jpeg"),
        limit=20,
        request_delay=0,
    )

    assert result == ImageDownloadResult(downloaded=1, skipped=1, stopped_reason=None)
    assert seen_urls == ["https://example.test/two.jpg"]
    assert json.loads(state.read_text(encoding="utf-8"))["offset"] == 2


def test_image_downloader_stops_on_rate_or_forbidden_status(tmp_path):
    raw = tmp_path / "raw" / "zhihu-575638886.jsonl"
    output_dir = tmp_path / "images" / "zhihu-575638886"
    state = tmp_path / "state" / "images-575638886.state.json"
    raw.parent.mkdir()
    raw.write_text(
        json.dumps({"id": "a1", "images": ["https://example.test/blocked.jpg"]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    result = download_zhihu_images(
        raw_path=raw,
        output_dir=output_dir,
        checkpoint_store=CheckpointStore(state),
        fetch_image=lambda url: (429, b"", "text/plain"),
        limit=20,
        request_delay=0,
    )

    assert result == ImageDownloadResult(downloaded=0, skipped=0, stopped_reason="http_429")
    assert not output_dir.exists()
    assert json.loads(state.read_text(encoding="utf-8"))["offset"] == 0


def test_cli_runs_zhihu_image_downloader_for_raw_jsonl(tmp_path):
    raw = tmp_path / "raw" / "zhihu-575638886.jsonl"
    state = tmp_path / "state" / "images-575638886.state.json"
    raw.parent.mkdir()
    raw.write_text(json.dumps({"id": "a1", "images": []}) + "\n", encoding="utf-8")

    exit_code = main(
        [
            "images",
            "zhihu",
            "--raw",
            str(raw),
            "--output-dir",
            str(tmp_path / "images" / "zhihu-575638886"),
            "--state",
            str(state),
            "--limit",
            "20",
            "--request-delay",
            "2",
        ]
    )

    assert exit_code == 0


def test_cli_rejects_unsafe_image_request_delay(tmp_path):
    raw = tmp_path / "raw" / "zhihu-575638886.jsonl"
    raw.parent.mkdir()
    raw.write_text(json.dumps({"id": "a1", "images": []}) + "\n", encoding="utf-8")

    exit_code = main(
        [
            "images",
            "zhihu",
            "--raw",
            str(raw),
            "--output-dir",
            str(tmp_path / "images" / "zhihu-575638886"),
            "--request-delay",
            "1",
        ]
    )

    assert exit_code == 1


def test_triage_image_downloader_downloads_only_selected_buckets_and_writes_manifest(tmp_path):
    report = tmp_path / "triage.json"
    output_dir = tmp_path / "images"
    manifest = tmp_path / "manifest.jsonl"
    state = tmp_path / "state.json"
    report.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "article_id": "zhihu_answer_1",
                        "article_title": "代码截图怎么理解？",
                        "article_url": "https://www.zhihu.com/question/1/answer/1",
                        "image_url": "https://example.test/keep.jpg",
                        "bucket": "keep_knowledge",
                        "reasons": ["knowledge_context"],
                    },
                    {
                        "article_id": "zhihu_answer_2",
                        "article_title": "头像壁纸分享",
                        "article_url": "https://www.zhihu.com/question/2/answer/2",
                        "image_url": "https://example.test/reject.jpg",
                        "bucket": "reject_decorative",
                        "reasons": ["decorative_title"],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seen_urls = []

    result = download_zhihu_triage_images(
        report_path=report,
        output_dir=output_dir,
        manifest_path=manifest,
        checkpoint_store=CheckpointStore(state),
        include_buckets={"keep_knowledge"},
        fetch_image=lambda url: seen_urls.append(url) or (200, b"image-bytes", "image/jpeg"),
        limit=20,
        request_delay=0,
    )

    assert result == ImageDownloadResult(downloaded=1, skipped=0, stopped_reason=None)
    assert seen_urls == ["https://example.test/keep.jpg"]
    saved_files = list(output_dir.iterdir())
    assert len(saved_files) == 1
    manifest_rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert manifest_rows[0]["article_id"] == "zhihu_answer_1"
    assert manifest_rows[0]["bucket"] == "keep_knowledge"
    assert manifest_rows[0]["path"].endswith(saved_files[0].name)


def test_triage_image_downloader_resumes_against_filtered_candidates(tmp_path):
    report = tmp_path / "triage.json"
    output_dir = tmp_path / "images"
    manifest = tmp_path / "manifest.jsonl"
    state = tmp_path / "state.json"
    report.write_text(
        json.dumps(
            {
                "items": [
                    {"image_url": "https://example.test/one.jpg", "bucket": "keep_knowledge", "article_id": "a1"},
                    {"image_url": "https://example.test/two.jpg", "bucket": "keep_knowledge", "article_id": "a2"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    CheckpointStore(state).save(offset=1, exported=1)
    seen_urls = []

    result = download_zhihu_triage_images(
        report_path=report,
        output_dir=output_dir,
        manifest_path=manifest,
        checkpoint_store=CheckpointStore(state),
        include_buckets={"keep_knowledge"},
        fetch_image=lambda url: seen_urls.append(url) or (200, b"image-bytes", "image/jpeg"),
        limit=20,
        request_delay=0,
    )

    assert result == ImageDownloadResult(downloaded=1, skipped=1, stopped_reason=None)
    assert seen_urls == ["https://example.test/two.jpg"]
    assert json.loads(state.read_text(encoding="utf-8"))["offset"] == 2


def test_triage_image_downloader_skips_gifs_by_default(tmp_path):
    report = tmp_path / "triage.json"
    output_dir = tmp_path / "images"
    manifest = tmp_path / "manifest.jsonl"
    state = tmp_path / "state.json"
    report.write_text(
        json.dumps(
            {
                "items": [
                    {"image_url": "https://example.test/motion.gif", "bucket": "keep_knowledge", "article_id": "a1"},
                    {"image_url": "https://example.test/still.jpg", "bucket": "keep_knowledge", "article_id": "a2"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seen_urls = []

    result = download_zhihu_triage_images(
        report_path=report,
        output_dir=output_dir,
        manifest_path=manifest,
        checkpoint_store=CheckpointStore(state),
        include_buckets={"keep_knowledge"},
        fetch_image=lambda url: seen_urls.append(url) or (200, b"image-bytes", "image/jpeg"),
        limit=20,
        request_delay=0,
    )

    assert result.downloaded == 1
    assert seen_urls == ["https://example.test/still.jpg"]


def test_triage_image_downloader_stops_cleanly_on_timeout(tmp_path):
    report = tmp_path / "triage.json"
    output_dir = tmp_path / "images"
    manifest = tmp_path / "manifest.jsonl"
    state = tmp_path / "state.json"
    report.write_text(
        json.dumps(
            {"items": [{"image_url": "https://example.test/slow.jpg", "bucket": "keep_knowledge", "article_id": "a1"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = download_zhihu_triage_images(
        report_path=report,
        output_dir=output_dir,
        manifest_path=manifest,
        checkpoint_store=CheckpointStore(state),
        include_buckets={"keep_knowledge"},
        fetch_image=lambda url: (_ for _ in ()).throw(TimeoutError("timed out")),
        limit=20,
        request_delay=0,
    )

    assert result == ImageDownloadResult(downloaded=0, skipped=0, stopped_reason="timeout")
    assert json.loads(state.read_text(encoding="utf-8"))["offset"] == 0


def test_triage_image_downloader_stops_cleanly_on_incomplete_read(tmp_path):
    report = tmp_path / "triage.json"
    output_dir = tmp_path / "images"
    manifest = tmp_path / "manifest.jsonl"
    state = tmp_path / "state.json"
    report.write_text(
        json.dumps(
            {"items": [{"image_url": "https://example.test/cut.jpg", "bucket": "keep_knowledge", "article_id": "a1"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = download_zhihu_triage_images(
        report_path=report,
        output_dir=output_dir,
        manifest_path=manifest,
        checkpoint_store=CheckpointStore(state),
        include_buckets={"keep_knowledge"},
        fetch_image=lambda url: (_ for _ in ()).throw(IncompleteRead(b"partial", 10)),
        limit=20,
        request_delay=0,
    )

    assert result == ImageDownloadResult(downloaded=0, skipped=0, stopped_reason="network_error")
    assert json.loads(state.read_text(encoding="utf-8"))["offset"] == 0


def test_triage_image_downloader_skips_missing_images_and_continues(tmp_path):
    report = tmp_path / "triage.json"
    output_dir = tmp_path / "images"
    manifest = tmp_path / "manifest.jsonl"
    state = tmp_path / "state.json"
    report.write_text(
        json.dumps(
            {
                "items": [
                    {"image_url": "https://example.test/missing.jpg", "bucket": "keep_knowledge", "article_id": "a1"},
                    {"image_url": "https://example.test/ok.jpg", "bucket": "keep_knowledge", "article_id": "a2"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fetch(url):
        if url.endswith("missing.jpg"):
            return 404, b"", "text/plain"
        return 200, b"image-bytes", "image/jpeg"

    result = download_zhihu_triage_images(
        report_path=report,
        output_dir=output_dir,
        manifest_path=manifest,
        checkpoint_store=CheckpointStore(state),
        include_buckets={"keep_knowledge"},
        fetch_image=fetch,
        limit=20,
        request_delay=0,
    )

    assert result == ImageDownloadResult(downloaded=1, skipped=1, stopped_reason=None)
    assert json.loads(state.read_text(encoding="utf-8"))["offset"] == 2


def test_cli_runs_zhihu_triage_image_downloader(tmp_path):
    report = tmp_path / "triage.json"
    state = tmp_path / "state.json"
    report.write_text(json.dumps({"items": []}), encoding="utf-8")

    exit_code = main(
        [
            "images",
            "zhihu-triage-download",
            "--report",
            str(report),
            "--output-dir",
            str(tmp_path / "images"),
            "--manifest",
            str(tmp_path / "manifest.jsonl"),
            "--state",
            str(state),
            "--request-delay",
            "2",
        ]
    )

    assert exit_code == 0
