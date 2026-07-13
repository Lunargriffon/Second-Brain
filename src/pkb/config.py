from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised when exporter configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    zhihu_cookie: str
    download_images: bool = False
    request_delay: float = 2.0
    max_retry: int = 3


def load_config(env_path: Path = Path(".env")) -> Config:
    values = read_env_file(env_path)
    cookie = values.get("ZHIHU_COOKIE", "").strip()
    if not cookie:
        raise ConfigError("ZHIHU_COOKIE is required")

    return Config(
        zhihu_cookie=cookie,
        download_images=_parse_bool(values.get("DOWNLOAD_IMAGES", "false")),
        request_delay=float(values.get("REQUEST_DELAY", "2")),
        max_retry=int(values.get("MAX_RETRY", "3")),
    )


def read_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


# Backward-compatible private alias for callers predating the public helper.
_read_env_file = read_env_file


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n", ""}:
        return False
    raise ConfigError(f"Invalid boolean value: {value}")
