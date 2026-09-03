from pathlib import Path
import json
import shutil
import subprocess

from pkb.douyin.manifest import ManifestStore
from pkb.douyin.media import MediaPaths
from pkb.douyin.models import FavoriteItem, TranscriptSegment
from pkb.douyin.pipeline import DouyinPipeline, DurableJsonlStore, MediaInfo
from pkb.douyin.transcription import TranscriptResult
from pkb.douyin.live import OpenCliFavoritesBrowser, YtDlpAcquirer, write_audit_atomic
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
        return subprocess.CompletedProcess(command, 0, "", "")

    favorite = _favorites()[0]
    destination = tmp_path / "video.mp4"
    YtDlpAcquirer(runner=runner, opencli_executable="opencli").acquire(favorite, destination)

    assert calls[1][:4] == ["opencli", "browser", "pkb-douyin-media", "open"]
    assert calls[2][:4] == ["opencli", "browser", "pkb-douyin-media", "eval"]
    assert calls[3][0] == "yt-dlp"
    assert "https://cdn.example/video.mp4" in calls[3]
    assert f"Referer:{favorite.url}" in calls[3]
