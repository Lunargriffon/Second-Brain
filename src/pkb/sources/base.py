from pathlib import Path
from typing import Mapping, Protocol

from pkb.knowledge.models import NormalizedDocument


class SourceAdapter(Protocol):
    source_name: str

    def normalize(
        self, record: Mapping[str, object], *, raw_path: Path, raw_line: int
    ) -> NormalizedDocument: ...
