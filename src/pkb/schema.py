from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any


class SchemaValidationError(ValueError):
    """Raised when an exported article does not match the raw schema."""


@lru_cache(maxsize=1)
def _article_schema() -> dict[str, Any]:
    schema_path = Path(__file__).resolve().parents[2] / "schema" / "article.v1.json"
    with schema_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def validate_article(article: dict[str, Any]) -> None:
    schema = _article_schema()
    required = schema["required"]
    properties = schema["properties"]

    for field in required:
        if field not in article:
            raise SchemaValidationError(f"Article schema validation failed: missing required field '{field}'")

    if not schema.get("additionalProperties", True):
        extra_fields = set(article) - set(properties)
        if extra_fields:
            field = sorted(extra_fields)[0]
            raise SchemaValidationError(f"Article schema validation failed at {field}: extra field is not allowed")

    for field, value in article.items():
        definition = properties[field]
        _validate_field(field, value, definition)


def _validate_field(field: str, value: Any, definition: dict[str, Any]) -> None:
    expected_type = definition.get("type")
    if expected_type == "string" and not isinstance(value, str):
        raise SchemaValidationError(f"Article schema validation failed at {field}: expected string")
    if expected_type == "array" and not isinstance(value, list):
        raise SchemaValidationError(f"Article schema validation failed at {field}: expected array")

    if "const" in definition and value != definition["const"]:
        raise SchemaValidationError(f"Article schema validation failed at {field}: expected {definition['const']!r}")

    if "enum" in definition and value not in definition["enum"]:
        raise SchemaValidationError(f"Article schema validation failed at {field}: invalid value {value!r}")

    if "pattern" in definition and isinstance(value, str) and not re.match(definition["pattern"], value):
        raise SchemaValidationError(f"Article schema validation failed at {field}: value does not match pattern")

    if definition.get("format") == "uri" and isinstance(value, str) and not _looks_like_uri(value):
        raise SchemaValidationError(f"Article schema validation failed at {field}: expected URI")

    if definition.get("format") == "date-time" and isinstance(value, str) and not _looks_like_datetime(value):
        raise SchemaValidationError(f"Article schema validation failed at {field}: expected ISO 8601 date-time")

    if expected_type == "array":
        item_type = definition.get("items", {}).get("type")
        if item_type == "string" and any(not isinstance(item, str) for item in value):
            raise SchemaValidationError(f"Article schema validation failed at {field}: expected string items")


def _looks_like_uri(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _looks_like_datetime(value: str) -> bool:
    if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$", value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True
