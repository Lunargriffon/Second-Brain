from pathlib import Path
import json
import shutil
import subprocess

from pkb.douyin.manifest import ManifestStore
from pkb.douyin.media import MediaPaths
from pkb.douyin.models import FavoriteItem, TranscriptSegment
from pkb.douyin.pipeline import DouyinPipeline, DurableJsonlStore, MediaInfo, RunAudit
from pkb.douyin.transcription import TranscriptResult
from pkb.douyin.live import (
    OpenCliFavoritesBrowser,
    YtDlpAcquirer,
    run_live_full,
    write_audit_atomic,
)
from pkb.knowledge.indexer import KnowledgeIndexer
from pkb.knowledge.repository import KnowledgeRepository
from pkb.knowledge.search import SearchIndex


class OfflineMedia:
    def __init__(self, root: Path):
        self.root = root

    def prepare(self, work_id: str) -> MediaPaths:
        work_dir = self.root / work_id
        work_dir.mkdir(parents=True, exist_ok=True)
        return MediaPaths(work_dir, work_dir / "video.mp4", work_dir / "audio.wav")

    def extract_audio(self, paths: MediaPaths) -> None:
        paths.audio.write_bytes(b"audio")

    def cleanup(self, work_dir: Path) -> None:
        for path in work_dir.iterdir():
            path.unlink()
        work_dir.rmdir()


class OfflineAcquirer:
    def acquire(self, favorite: FavoriteItem, destination: Path) -> None:
        destination.write_bytes(favorite.work_id.encode())


class OfflineTranscriber:
    def transcribe(self, audio_path: Path, voiced_seconds: float) -> TranscriptResult:
        number = int(audio_path.parent.name)
        text = "第七条口述知识" if number == 7 else f"第{number}条收藏内容"
        return TranscriptResult(text, (TranscriptSegment(0, 1, text),), "offline", "fixture", "zh")


def _favorites() -> list[FavoriteItem]:
    return [
        FavoriteItem(
            str(number),
            f"https://www.douyin.com/video/{number}",
            f"author-{number}",
            "测试作者",
            f"收藏 {number}",
            ("知识",),
            None,
            "2026-07-18T00:00:00Z",
        )
        for number in range(1, 21)
    ]


class FullRunBrowser:
    def __init__(self, pages):
        self.pages = iter(pages)

    def page(self, _cursor):
        from pkb.douyin.collector import FavoritePage

        values = next(self.pages)
        return FavoritePage(
            tuple(
                {
                    "aweme_id": value.work_id,
                    "share_url": value.url,
                    "author": {"uid": value.author_id, "nickname": value.author},
                }
                for value in values
            ),
            str(len(values)),
            "2026-09-05T00:00:00Z",
        )


class RecordingPipeline:
    def __init__(self, manifest, events):
        self.manifest = manifest
        self.events = events

    def run(self):
        self.events.append(("pipeline", [item.work_id for item in self.manifest.items()]))
        return RunAudit({"selected": 3}, {}, False, 0)


def test_full_run_checkpoints_discovery_before_processing(tmp_path):
    events = []
    favorites = _favorites()[:3]
    browser = FullRunBrowser([
        favorites[:2],
        favorites,
        favorites,
        favorites,
        favorites,
    ])
    state = tmp_path / "state.json"
    manifest = ManifestStore(state)
    manifest.discover(favorites[:1])

    audit = run_live_full(
        output=tmp_path / "raw.jsonl",
        state=state,
        report=tmp_path / "audit.json",
        temp_root=tmp_path / "media",
        request_delay=7,
        browser=browser,
        pipeline_factory=lambda store: RecordingPipeline(store, events),
        delay=lambda _seconds: None,
    )

    assert events == [("pipeline", ["1", "2", "3"])]
    assert [item.work_id for item in manifest.items()] == ["1", "2", "3"]
    assert audit.discovery_complete is True
    assert audit.discovered == 2


def test_offline_douyin_trial_is_resumable_searchable_and_clean(tmp_path):
    raw_dir = tmp_path / "raw"
    temp_root = tmp_path / "temp"
    manifest = ManifestStore(tmp_path / "state.json")
    manifest.discover(_favorites())
    pipeline = DouyinPipeline(
        manifest=manifest,
        raw_store=DurableJsonlStore(raw_dir / "douyin-favorites.jsonl"),
        media=OfflineMedia(temp_root),
        acquirer=OfflineAcquirer(),
        transcriber=OfflineTranscriber(),
        probe=lambda _paths: MediaInfo(1.0, 1.0),
    )

    first = pipeline.run()
    second = pipeline.run()

    assert first.counts["selected"] == 20
    assert first.counts["cleaned"] == 20
    assert second.counts.get("persisted", 0) == 0
    assert list(temp_root.rglob("*.mp4")) == []
    assert list(temp_root.rglob("*.wav")) == []

    with KnowledgeRepository(tmp_path / "knowledge.db") as repository:
        report = KnowledgeIndexer(repository).build(raw_dir, strict=True)
        results = SearchIndex(repository).search("第七条口述知识", source="douyin")
        assert report.created == 20
        assert results[0].identity == "douyin:work:7"


def test_live_audit_is_replaced_atomically_without_private_details(tmp_path):
    report = tmp_path / "audit.json"
    write_audit_atomic(
        report,
        {"counts": {"selected": 1}, "errors": {"auth_required": 1}, "stopped": True,
         "cleanup_pending": 0},
    )

    assert '"auth_required"' in report.read_text(encoding="utf-8")
    assert not report.with_suffix(".json.tmp").exists()


def test_browser_stops_pagination_when_scroll_reveals_no_new_favorites():
    payload = json.dumps({"entries": [{"attrs": {"href": "https://www.douyin.com/video/7"}}]})

    def runner(command, **_kwargs):
        output = json.dumps({"logged_in": True}) if "whoami" in command else ("" if "open" in command else payload)
        return subprocess.CompletedProcess(command, 0, output, "")

    browser = OpenCliFavoritesBrowser(runner=runner)
    assert browser.executable == (shutil.which("opencli") or "opencli")
    assert browser.page(None).cursor == "1"
    assert browser.page("1").cursor is None


def test_downloader_falls_back_to_opencli_media_url_when_chrome_dpapi_fails(tmp_path):
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        if command[0] == "yt-dlp" and "--cookies-from-browser" in command:
            raise subprocess.CalledProcessError(1, command, stderr="Failed to decrypt with DPAPI")
        if command[0] == "opencli" and "eval" in command:
            return subprocess.CompletedProcess(command, 0, "https://cdn.example/video.mp4", "")
        if command[0] == "opencli" and "network" in command:
            return subprocess.CompletedProcess(command, 0, '{"entries":[]}', "")
        if command[0] == "opencli" and "open" in command:
            return subprocess.CompletedProcess(command, 0, '{"page":"tab-1"}', "")
        return subprocess.CompletedProcess(command, 0, "", "")

    favorite = _favorites()[0]
    session = f"pkb-douyin-media-{favorite.work_id}"
    destination = tmp_path / "video.mp4"
    YtDlpAcquirer(runner=runner, opencli_executable="opencli").acquire(favorite, destination)

    assert calls[1][:5] == [
        "opencli",
        "browser",
        session,
        "open",
        "https://www.douyin.com/",
    ]
    assert any(command[:4] == ["opencli", "browser", session, "eval"] for command in calls)
    direct_download = next(command for command in calls if command[0] == "yt-dlp" and "--add-header" in command)
    assert "https://cdn.example/video.mp4" in direct_download
    assert f"Referer:{favorite.url}" in direct_download
    assert calls[-1] == ["opencli", "browser", session, "close"]


def test_downloader_extracts_https_media_from_captured_detail_for_blob_video(tmp_path):
    calls = []
    favorite = _favorites()[0]

    def runner(command, **_kwargs):
        calls.append(command)
        if command[0] == "yt-dlp" and "--cookies-from-browser" in command:
            raise subprocess.CalledProcessError(1, command, stderr="Failed to decrypt with DPAPI")
        if command[0] == "opencli" and command[3:5] == [
            "open",
            "https://www.douyin.com/",
        ]:
            return subprocess.CompletedProcess(command, 0, '{"page":"tab-1"}', "")
        if command[0] == "opencli" and "eval" in command:
            return subprocess.CompletedProcess(command, 0, "blob:https://www.douyin.com/id", "")
        if command[0] == "opencli" and "--detail" in command:
            body = {
                "key": "detail",
                "body": {
                    "aweme_detail": {
                        "aweme_id": favorite.work_id,
                        "video": {"play_addr": {"url_list": ["https://cdn.example/blob.mp4"]}},
                    }
                },
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(body), "")
        if command[0] == "opencli" and "network" in command:
            payload = {"entries": [{"key": "GET www.douyin.com/aweme/v1/web/aweme/detail/"}]}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[0] == "opencli" and "open" in command:
            return subprocess.CompletedProcess(command, 0, '{"page":"tab-1"}', "")
        return subprocess.CompletedProcess(command, 0, "", "")

    YtDlpAcquirer(runner=runner, opencli_executable="opencli").acquire(
        favorite, tmp_path / "video.mp4"
    )

    direct_download = next(command for command in calls if command[0] == "yt-dlp" and "--add-header" in command)
    assert "https://cdn.example/blob.mp4" in direct_download
    assert f"Referer:{favorite.url}" in direct_download
    assert calls[-1] == [
        "opencli",
        "browser",
        f"pkb-douyin-media-{favorite.work_id}",
        "close",
    ]


def test_downloader_rechecks_capture_when_detail_arrives_late(tmp_path):
    favorite = _favorites()[0]
    capture_reads = 0

    def runner(command, **_kwargs):
        nonlocal capture_reads
        if command[0] == "yt-dlp" and "--cookies-from-browser" in command:
            raise subprocess.CalledProcessError(1, command, stderr="Failed to decrypt with DPAPI")
        if command[0] == "opencli" and command[3:5] == [
            "open",
            "https://www.douyin.com/",
        ]:
            return subprocess.CompletedProcess(command, 0, '{"page":"tab-1"}', "")
        if command[0] == "opencli" and "eval" in command:
            return subprocess.CompletedProcess(command, 0, "blob:https://www.douyin.com/id", "")
        if command[0] == "opencli" and "--detail" in command:
            payload = {
                "body": {
                    "aweme_detail": {
                        "aweme_id": favorite.work_id,
                        "video": {"play_addr": {"url_list": ["https://cdn.example/late.mp4"]}},
                    }
                }
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[0] == "opencli" and "network" in command:
            capture_reads += 1
            entries = [] if capture_reads < 3 else [
                {"key": "GET www.douyin.com/aweme/v1/web/aweme/detail/"}
            ]
            return subprocess.CompletedProcess(command, 0, json.dumps({"entries": entries}), "")
        if command[0] == "opencli" and "open" in command:
            return subprocess.CompletedProcess(command, 0, '{"page":"tab-1"}', "")
        return subprocess.CompletedProcess(command, 0, "", "")

    YtDlpAcquirer(runner=runner, opencli_executable="opencli").acquire(
        favorite, tmp_path / "video.mp4"
    )

    assert capture_reads == 3
