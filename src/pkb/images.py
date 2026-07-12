from __future__ import annotations

import hashlib
import json
import socket
import time
from dataclasses import dataclass
from http.client import IncompleteRead
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pkb.checkpoint import CheckpointStore

FetchImage = Callable[[str], tuple[int, bytes, str]]


@dataclass(frozen=True)
class ImageDownloadResult:
    downloaded: int
    skipped: int
    stopped_reason: str | None = None


def download_zhihu_images(
    *,
    raw_path: Path,
    output_dir: Path,
    checkpoint_store: CheckpointStore,
    fetch_image: FetchImage | None = None,
    limit: int = 20,
    request_delay: float = 2.0,
) -> ImageDownloadResult:
    fetch = fetch_image or _fetch_image
    checkpoint = checkpoint_store.load()
    downloaded = 0
    skipped = 0
    offset = checkpoint.offset

    for image_index, url in enumerate(_iter_image_urls(raw_path)):
        if image_index < checkpoint.offset:
            skipped += 1
            continue
        if limit and downloaded >= limit:
            break

        try:
            status, body, content_type = fetch(url)
        except TimeoutError:
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            return ImageDownloadResult(downloaded=downloaded, skipped=skipped, stopped_reason="timeout")
        except (IncompleteRead, OSError, URLError):
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            return ImageDownloadResult(downloaded=downloaded, skipped=skipped, stopped_reason="network_error")
        if status in {403, 429}:
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            return ImageDownloadResult(downloaded=downloaded, skipped=skipped, stopped_reason=f"http_{status}")
        if status in {404, 410}:
            offset = image_index + 1
            skipped += 1
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            continue
        if status in {404, 410}:
            offset = image_index + 1
            skipped += 1
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            continue
        if status in {404, 410}:
            offset = image_index + 1
            skipped += 1
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            continue
        if status >= 400:
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            return ImageDownloadResult(downloaded=downloaded, skipped=skipped, stopped_reason=f"http_{status}")

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / _image_filename(url, content_type)
        output_path.write_bytes(body)
        offset = image_index + 1
        downloaded += 1
        checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)

        if request_delay > 0:
            time.sleep(request_delay)

    return ImageDownloadResult(downloaded=downloaded, skipped=skipped, stopped_reason=None)


def download_zhihu_triage_images(
    *,
    report_path: Path,
    output_dir: Path,
    manifest_path: Path,
    checkpoint_store: CheckpointStore,
    include_buckets: set[str],
    include_gifs: bool = False,
    fetch_image: FetchImage | None = None,
    limit: int = 20,
    request_delay: float = 2.0,
) -> ImageDownloadResult:
    fetch = fetch_image or _fetch_image
    checkpoint = checkpoint_store.load()
    downloaded = 0
    skipped = 0
    offset = checkpoint.offset

    for image_index, item in enumerate(_iter_triage_items(report_path, include_buckets, include_gifs=include_gifs)):
        if image_index < checkpoint.offset:
            skipped += 1
            continue
        if limit and downloaded >= limit:
            break

        url = item["image_url"]
        try:
            status, body, content_type = fetch(url)
        except TimeoutError:
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            return ImageDownloadResult(downloaded=downloaded, skipped=skipped, stopped_reason="timeout")
        except (IncompleteRead, OSError, URLError):
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            return ImageDownloadResult(downloaded=downloaded, skipped=skipped, stopped_reason="network_error")
        if status in {404, 410}:
            offset = image_index + 1
            skipped += 1
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            continue
        if status >= 400:
            checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)
            return ImageDownloadResult(downloaded=downloaded, skipped=skipped, stopped_reason=f"http_{status}")

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / _image_filename(url, content_type)
        output_path.write_bytes(body)
        _append_manifest_row(manifest_path, item, output_path)
        offset = image_index + 1
        downloaded += 1
        checkpoint_store.save(offset=offset, exported=checkpoint.exported + downloaded)

        if request_delay > 0:
            time.sleep(request_delay)

    return ImageDownloadResult(downloaded=downloaded, skipped=skipped, stopped_reason=None)


def _iter_image_urls(raw_path: Path):
    for line in raw_path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        for url in record.get("images", []):
            if isinstance(url, str) and url:
                yield url


def _iter_triage_items(report_path: Path, include_buckets: set[str], *, include_gifs: bool):
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    seen_urls: set[str] = set()
    for item in report.get("items", []):
        url = item.get("image_url")
        bucket = item.get("bucket")
        if not isinstance(url, str) or not url:
            continue
        if bucket not in include_buckets:
            continue
        if not include_gifs and _is_gif_url(url):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        yield item


def _is_gif_url(url: str) -> bool:
    return Path(urlparse(url).path).suffix.lower() == ".gif"


def _append_manifest_row(manifest_path: Path, item: dict, output_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "article_id": item.get("article_id", ""),
        "article_title": item.get("article_title", ""),
        "article_url": item.get("article_url", ""),
        "image_url": item.get("image_url", ""),
        "bucket": item.get("bucket", ""),
        "reasons": item.get("reasons", []),
        "path": str(output_path),
    }
    with manifest_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        file.write("\n")


def _image_filename(url: str, content_type: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return f"{digest}{suffix}"
    if content_type == "image/png":
        return f"{digest}.png"
    if content_type == "image/webp":
        return f"{digest}.webp"
    if content_type == "image/gif":
        return f"{digest}.gif"
    return f"{digest}.jpg"


def _fetch_image(url: str) -> tuple[int, bytes, str]:
    request = Request(url, headers={"User-Agent": "pkb/0.1"})
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read(), response.headers.get_content_type()
    except HTTPError as exc:
        return exc.code, b"", exc.headers.get_content_type()
    except IncompleteRead as exc:
        raise URLError("image response was incomplete") from exc
    except socket.timeout as exc:
        raise TimeoutError("image request timed out") from exc
