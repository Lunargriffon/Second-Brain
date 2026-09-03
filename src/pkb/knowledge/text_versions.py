"""Normalization and exact-span grounding for immutable document text versions."""

from __future__ import annotations

import unicodedata


class GroundingError(ValueError):
    """Raised when an excerpt is not grounded at its declared text span."""


def normalize_plain_text(value: str) -> str:
    """Return NFKC text with every newline convention represented as LF."""
    return unicodedata.normalize(
        "NFKC", value.replace("\r\n", "\n").replace("\r", "\n")
    )


def _fold_whitespace(value: str) -> str:
    return "".join(value.split())


def validate_span(text: str, start: int, end: int, excerpt: str) -> str:
    """Validate a zero-based, Unicode-code-point, half-open excerpt span."""
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
    ):
        raise GroundingError("offsets must form a positive half-open range")
    if end > len(text) or text[start:end] != excerpt:
        raise GroundingError("excerpt does not match exact span")
    if _fold_whitespace(excerpt) not in _fold_whitespace(text):
        raise GroundingError("excerpt was not found in normalized source text")
    return excerpt
