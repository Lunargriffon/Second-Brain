"""Local-only composition for the authenticated Douyin favorites trial."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping

from .collector import CollectionStopped, FavoritePage, FavoritesCollector
from .manifest import ManifestStore
from .media import AcquisitionFailure, TemporaryMedia
from .models import FavoriteItem
from .pipeline import DouyinPipeline, DurableJsonlStore, MediaInfo, RunAudit
from .transcription import FallbackTranscriber, FasterWhisperEngine, SenseVoiceEngine


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
        self.opened = False
        self.last_count = -1

    def page(self, cursor: str | None) -> FavoritePage:
        if not self.opened:
            self._run(["opencli", "browser", self.session, "open", self.FAVORITES_URL])
            self.opened = True
        elif cursor is not None:
            self._run(["opencli", "browser", self.session, "eval", "window.scrollTo(0,document.body.scrollHeight); true"])

        script = r"""(() => {
const body=(document.body?.innerText||'').toLowerCase();
if (/验证码|captcha|安全验证/.test(body)) return {error:'captcha_required'};
if (/登录后|立即登录|扫码登录/.test(body)) return {error:'login_required'};
const seen=new Set(), items=[];
for (const a of document.querySelectorAll('a[href*="/video/"]')) {
  const m=a.href.match(/\/video\/(\d+)/); if (!m || seen.has(m[1])) continue;
  seen.add(m[1]);
  const text=(a.innerText||a.getAttribute('aria-label')||'').trim();
  items.push({aweme_id:m[1],share_url:a.href.split('?')[0],desc:text,
    author:{uid:'unknown',nickname:''},text_extra:[]});
}
return {items, cursor:String(items.length), has_more:items.length>0,
 observed_at:new Date().toISOString()};
})()"""
        completed = self._run(["opencli", "browser", self.session, "eval", script])
        try:
            value = json.loads(completed.stdout)
            if isinstance(value, Mapping) and "result" in value and isinstance(value["result"], Mapping):
                value = value["result"]
            if not isinstance(value, Mapping):
                raise ValueError
        except (json.JSONDecodeError, ValueError, TypeError):
            raise CollectionStopped("browser_unavailable") from None
        page = FavoritePage.from_result(value)
        current_count = len(page.items)
        if cursor is not None and current_count <= self.last_count:
            page = FavoritePage(page.items, None, page.observed_at)
        self.last_count = max(self.last_count, current_count)
        return page

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(command, check=True, capture_output=True, text=True)
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
    def __init__(self, *, runner: Runner = subprocess.run) -> None:
        self.runner = runner

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
            code = "http_429" if "429" in detail else "http_403" if "403" in detail else (
                "auth_required" if "login" in detail or "cookie" in detail else
                "unavailable" if "unavailable" in detail or "not found" in detail else
                "download_failed"
            )
            raise AcquisitionFailure(code) from None


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
        discovered = FavoritesCollector(
            OpenCliFavoritesBrowser(), request_delay=request_delay
        ).collect(limit=limit)
        manifest.discover(discovered)
        pipeline = DouyinPipeline(
            manifest=manifest,
            raw_store=DurableJsonlStore(output),
            media=TemporaryMedia(temp_root),
            acquirer=YtDlpAcquirer(),
            transcriber=FallbackTranscriber(SenseVoiceEngine(), FasterWhisperEngine()),
            probe=probe_audio,
        )
        audit = pipeline.run()
    except CollectionStopped as exc:
        audit = RunAudit(
            counts={"selected": 0}, errors={exc.code: 1}, stopped=True,
            cleanup_pending=sum(item.stage.value in {"persisted", "indexed"} for item in manifest.items()),
        )
    write_audit_atomic(report, asdict(audit))
    return audit
