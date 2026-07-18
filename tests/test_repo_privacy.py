from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from pkb.cli import _default_douyin_temp_root


def test_generated_vault_projection_is_git_ignored() -> None:
    root = Path(__file__).resolve().parents[1]
    generated = (
        "vault/.pkb-generated.json",
        "vault/articles/private.md",
        "vault/sources/private.md",
        "vault/topics/private.md",
        "vault/tags/private.md",
        "vault/collections/private.md",
        "vault/attachments/private.bin",
    )

    ignored = {
        path
        for path in generated
        if subprocess.run(
            ["git", "check-ignore", "--quiet", path], cwd=root, check=False
        ).returncode
        == 0
    }

    assert ignored == set(generated)


def test_douyin_default_temp_root_is_outside_repository() -> None:
    root = Path(__file__).resolve().parents[1]
    temp_root = _default_douyin_temp_root()

    assert temp_root.is_relative_to(Path(tempfile.gettempdir()))
    assert not temp_root.is_relative_to(root)
