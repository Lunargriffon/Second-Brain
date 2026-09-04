"""Safe subprocess boundary for read-only OpenCLI commands."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Callable


class OpenCliError(RuntimeError):
    """An OpenCLI failure represented by a stable, non-sensitive code."""


class OpenCliGateway:
    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        executable: str | None = None,
    ) -> None:
        self.runner = runner
        self.executable = executable or shutil.which("opencli") or "opencli"

    def run_json(self, arguments: list[str]) -> dict[str, Any] | list[Any]:
        try:
            completed = self.runner(
                [self.executable, *arguments],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except FileNotFoundError:
            raise OpenCliError("opencli_unavailable") from None
        except subprocess.TimeoutExpired:
            raise OpenCliError("opencli_timeout") from None
        except subprocess.CalledProcessError:
            raise OpenCliError("opencli_failed") from None

        try:
            value = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError):
            raise OpenCliError("opencli_invalid_json") from None
        if not isinstance(value, (dict, list)):
            raise OpenCliError("opencli_invalid_json")
        return value
