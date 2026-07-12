from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pkb.schema import validate_article


class JsonlWriter:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path

    def write(self, article: dict[str, Any]) -> None:
        validate_article(article)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(article, ensure_ascii=False, sort_keys=True))
            file.write("\n")
