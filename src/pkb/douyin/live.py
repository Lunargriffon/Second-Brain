"""Local-only composition for the authenticated Douyin favorites trial."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from .collector import CollectionStopped, FavoritePage, FavoritesBrowser, FavoritesCollector
from .manifest import ManifestStore
from .media import AcquisitionFailure, TemporaryMedia
from .models import FavoriteItem
from .pipeline import DouyinPipeline, DurableJsonlStore, MediaInfo, RunAudit
from .transcription import FallbackTranscriber, FasterWhisperEngine, SenseVoiceEngine
from pkb.opencli_gateway import OpenCliError, OpenCliGateway


Runner = Callable[..., subprocess.CompletedProcess[str]]


def write_audit_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class OpenCliFavoritesBrowser:
    """Read visible favorites from the user's existing local Chrome session."""

    FAVORITES_URL = "https://www.douyin.com/user/self?showTab=favorite_collection"

    def __init__(self, *, runner: Runner = subprocess.run, session: str = "pkb-douyin") -> None:
        self.runner = runner
        self.session = session
        self.executable = shutil.which("opencli") or "opencli"
        self.opened = False
        self.last_count = -1

    def page(self, cursor: str | None) -> FavoritePage:
        if not self.opened:
            auth = self._run([self.executable, "douyin", "whoami", "-f", "json"])
            try:
                identity = json.loads(auth.stdout)
            except json.JSONDecodeError:
                raise CollectionStopped("browser_unavailable") from None
            if not isinstance(identity, Mapping) or identity.get("logged_in") is not True:
                raise CollectionStopped("auth_required")
            self._run([self.executable, "browser", self.session, "open", self.FAVORITES_URL])
            self.opened = True
        elif cursor is not None:
            self._run([self.executable, "browser", self.session, "eval", "window.scrollTo(0,document.body.scrollHeight); true"])

        self._run([self.executable, "browser", self.session, "wait", "time", "2"])
        script = (
            "Array.from(document.links)"
            ".filter(function(link){return link.href.includes('/video/');})"
            ".filter(function(link){return link.closest('ul');})"
            ".filter(function(link){return link.closest('footer')===null;})"
            ".map(function(link){return link.href;})"
        )
        completed = self._run(
            [self.executable, "browser", self.session, "eval", script]
        )
        try:
            value = json.loads(completed.stdout)
            if not isinstance(value, list):
                raise ValueError
        except (json.JSONDecodeError, ValueError, TypeError):
            raise CollectionStopped("browser_unavailable") from None
        raw_items = []
        for link in value:
            if not isinstance(link, str) or "/video/" not in link:
                continue
            work_id = link.split("/video/", 1)[1].split("?", 1)[0].split("/", 1)[0]
            if work_id.isdigit():
                raw_items.append({
                    "aweme_id": work_id,
                    "share_url": f"https://www.douyin.com/video/{work_id}",
                    "author": {"uid": "unknown", "nickname": ""},
                })
        normalized = {
            "items": raw_items,
            "cursor": str(len(raw_items)),
            "has_more": bool(raw_items),
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        page = FavoritePage.from_result(normalized)
        current_count = len(page.items)
        if cursor is not None and current_count <= self.last_count:
            page = FavoritePage(page.items, None, page.observed_at)
        self.last_count = max(self.last_count, current_count)
        return page

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            stderr = str(getattr(exc, "stderr", "")).lower()
            if "429" in stderr:
                code = "http_429"
            elif "403" in stderr:
                code = "http_403"
            elif "captcha" in stderr or "验证码" in stderr:
                code = "captcha"
            elif "login" in stderr or "auth" in stderr:
                code = "auth_required"
            else:
                code = "browser_unavailable"
            raise CollectionStopped(code) from None


class YtDlpAcquirer:
    def __init__(
        self, *, runner: Runner = subprocess.run, opencli_executable: str | None = None
    ) -> None:
        self.runner = runner
        self.opencli_executable = opencli_executable or shutil.which("opencli") or "opencli"

    def acquire(self, favorite: FavoriteItem, destination: Path) -> None:
        command = [
            "yt-dlp", "--cookies-from-browser", "chrome", "--no-playlist",
            "--max-filesize", "2G", "-o", str(destination), favorite.url,
        ]
        try:
            self.runner(command, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            raise AcquisitionFailure("downloader_unavailable") from None
        except subprocess.CalledProcessError as exc:
            detail = str(exc.stderr).lower()
            if "dpapi" in detail or "could not copy chrome cookie database" in detail:
                self._acquire_from_opencli(favorite, destination)
                return
            code = "http_429" if "429" in detail else "http_403" if "403" in detail else (
                "auth_required" if "login" in detail or "cookie" in detail else
                "unavailable" if "unavailable" in detail or "not found" in detail else
                "download_failed"
            )
            raise AcquisitionFailure(code) from None

    def _acquire_from_opencli(self, favorite: FavoriteItem, destination: Path) -> None:
        session = f"pkb-douyin-media-{favorite.work_id}"
        javascript = "document.querySelector('video')?.currentSrc||document.querySelector('video')?.src||''"
        gateway = OpenCliGateway(runner=self.runner, executable=self.opencli_executable)
        try:
            opened = gateway.run_json(
                ["browser", session, "open", "https://www.douyin.com/"]
            )
            tab = opened.get("page") if isinstance(opened, Mapping) else None
            if not isinstance(tab, str) or not tab:
                raise AcquisitionFailure("browser_media_failed")
            gateway.run_json(["browser", session, "network", "--tab", tab])
            gateway.run_json(["browser", session, "open", "--tab", tab, favorite.url])
            result = self.runner(
                [self.opencli_executable, "browser", session, "eval", "--tab", tab, javascript],
                check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            media_url = result.stdout.strip()
            parsed = urlparse(media_url)
            if parsed.scheme != "https" or not parsed.netloc:
                media_url = self._captured_media_url(gateway, session, tab, favorite.work_id)
            self.runner(
                ["yt-dlp", "--add-header", f"Referer:{favorite.url}", "--no-playlist",
                 "--max-filesize", "2G", "-o", str(destination), media_url],
                check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            raise AcquisitionFailure("downloader_unavailable") from None
        except subprocess.CalledProcessError:
            raise AcquisitionFailure("browser_media_failed") from None
        except OpenCliError as exc:
            raise AcquisitionFailure(str(exc)) from None
        finally:
            try:
                self.runner(
                    [self.opencli_executable, "browser", session, "close"],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                pass

    @staticmethod
    def _captured_media_url(
        gateway: OpenCliGateway, session: str, tab: str, work_id: str
    ) -> str:
        for _attempt in range(3):
            capture = gateway.run_json(["browser", session, "network", "--tab", tab])
            entries = capture.get("entries") if isinstance(capture, Mapping) else None
            if not isinstance(entries, list):
                continue
            keys = [
                entry.get("key") for entry in entries
                if isinstance(entry, Mapping)
                and isinstance(entry.get("key"), str)
                and "/aweme/detail" in str(entry.get("key"))
            ]
            for key in reversed(keys):
                detail = gateway.run_json(["browser", session, "network", "--detail", key])
                body = detail.get("body") if isinstance(detail, Mapping) else None
                aweme = body.get("aweme_detail") if isinstance(body, Mapping) else None
                if not isinstance(aweme, Mapping) or str(aweme.get("aweme_id")) != work_id:
                    continue
                video = aweme.get("video")
                play = video.get("play_addr") if isinstance(video, Mapping) else None
                urls = play.get("url_list") if isinstance(play, Mapping) else None
                if isinstance(urls, list):
                    for value in urls:
                        if isinstance(value, str):
                            parsed = urlparse(value)
                            if parsed.scheme == "https" and parsed.netloc:
                                return value
        raise AcquisitionFailure("media_url_unavailable")


def probe_audio(paths: Any, *, runner: Runner = subprocess.run) -> MediaInfo:
    try:
        result = runner(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(paths.audio)],
            check=True, capture_output=True, text=True,
        )
        duration = float(result.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        raise RuntimeError("media_probe_failed") from None
    return MediaInfo(duration, duration)


@dataclass(frozen=True)
class FullRunAudit:
    counts: dict[str, int]
    errors: dict[str, int]
    stopped: bool
    cleanup_pending: int
    discovered: int
    discovery_complete: bool
    index_refreshed: bool = False
    wiki_refreshed: bool = False


def _build_pipeline(
    manifest: ManifestStore, output: Path, temp_root: Path
) -> DouyinPipeline:
    return DouyinPipeline(
        manifest=manifest,
        raw_store=DurableJsonlStore(output),
        media=TemporaryMedia(temp_root),
        acquirer=YtDlpAcquirer(),
        transcriber=FallbackTranscriber(SenseVoiceEngine(), FasterWhisperEngine()),
        probe=probe_audio,
    )


def run_live_full(
    *,
    output: Path,
    state: Path,
    report: Path,
    temp_root: Path,
    request_delay: float,
    browser: FavoritesBrowser | None = None,
    delay: Callable[[float], None] = time.sleep,
    pipeline_factory: Callable[[ManifestStore], DouyinPipeline] | None = None,
) -> FullRunAudit:
    manifest = ManifestStore(state)
    known = {item.work_id for item in manifest.items()}
    collector = FavoritesCollector(
        browser or OpenCliFavoritesBrowser(),
        delay=delay,
        request_delay=request_delay,
    )
    try:
        discovered = collector.collect_all(
            known_ids=known,
            on_discovered=manifest.discover,
        )
        pipeline = (
            pipeline_factory(manifest)
            if pipeline_factory is not None
            else _build_pipeline(manifest, output, temp_root)
        )
        run = pipeline.run()
        audit = FullRunAudit(
            counts=run.counts,
            errors=run.errors,
            stopped=run.stopped,
            cleanup_pending=run.cleanup_pending,
            discovered=len(discovered),
            discovery_complete=True,
        )
    except CollectionStopped as exc:
        audit = FullRunAudit(
            counts={"selected": 0},
            errors={exc.code: 1},
            stopped=True,
            cleanup_pending=sum(
                item.stage.value in {"persisted", "indexed"}
                for item in manifest.items()
            ),
            discovered=len(
                {item.work_id for item in manifest.items()} - known
            ),
            discovery_complete=False,
        )
    write_audit_atomic(report, asdict(audit))
    return audit


def run_live_trial(
    *, output: Path, state: Path, report: Path, temp_root: Path,
    limit: int, request_delay: float,
) -> RunAudit:
    if not 1 <= limit <= 20:
        raise ValueError("trial limit must be 1..20")
    if not 5 <= request_delay <= 10:
        raise ValueError("request delay must be 5..10 seconds")
    manifest = ManifestStore(state)
    try:
        if not manifest.items():
            discovered = FavoritesCollector(
                OpenCliFavoritesBrowser(), request_delay=request_delay
            ).collect(limit=limit)
            manifest.discover(discovered)
        pipeline = _build_pipeline(manifest, output, temp_root)
        audit = pipeline.run()
    except CollectionStopped as exc:
        audit = RunAudit(
            counts={"selected": 0}, errors={exc.code: 1}, stopped=True,
            cleanup_pending=sum(item.stage.value in {"persisted", "indexed"} for item in manifest.items()),
        )
    write_audit_atomic(report, asdict(audit))
    return audit
