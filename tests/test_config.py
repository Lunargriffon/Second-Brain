from pathlib import Path

import pytest

from pkb.config import ConfigError, load_config


def write_env(path: Path, content: str) -> Path:
    env_path = path / ".env"
    env_path.write_text(content, encoding="utf-8")
    return env_path


def test_load_config_reads_cookie_and_defaults(tmp_path):
    env_path = write_env(tmp_path, "ZHIHU_COOKIE=abc=123\n")

    config = load_config(env_path)

    assert config.zhihu_cookie == "abc=123"
    assert config.download_images is False
    assert config.request_delay == 2.0
    assert config.max_retry == 3


def test_load_config_reads_utf8_bom_env_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("ZHIHU_COOKIE=abc=123\n", encoding="utf-8-sig")

    config = load_config(env_path)

    assert config.zhihu_cookie == "abc=123"


def test_load_config_parses_export_options(tmp_path):
    env_path = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIHU_COOKIE=abc=123",
                "DOWNLOAD_IMAGES=true",
                "REQUEST_DELAY=0.5",
                "MAX_RETRY=5",
            ]
        ),
    )

    config = load_config(env_path)

    assert config.download_images is True
    assert config.request_delay == 0.5
    assert config.max_retry == 5


def test_load_config_requires_cookie(tmp_path):
    env_path = write_env(tmp_path, "DOWNLOAD_IMAGES=false\n")

    with pytest.raises(ConfigError):
        load_config(env_path)
