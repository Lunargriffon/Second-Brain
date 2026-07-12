from dataclasses import FrozenInstanceError

import pytest

from pkb.derive.models import DerivationValidationError, validate_article_derivation


def valid_payload() -> dict[str, object]:
    return {
        "summary": "先理解再练习",
        "key_points": ["先理解"],
        "topics": [{"name": "学习", "confidence": 0.9}],
        "tags": [{"name": "方法", "confidence": 0.8}],
        "content_type": "tutorial",
        "evergreen_score": 4,
        "reading_priority": 3,
        "priority_reason": "可复用",
        "source_citations": [{"claim": "先理解", "excerpt": "先理解，再练习"}],
    }


def test_derivation_requires_grounded_citations():
    result = validate_article_derivation(valid_payload(), source_text="先理解，\n再练习。")

    assert result.summary == "先理解再练习"
    assert result.tags[0].name == "方法"
    assert result.source_citations[0].claim == "先理解"
    with pytest.raises(FrozenInstanceError):
        result.summary = "changed"  # type: ignore[misc]


def test_derivation_rejects_missing_excerpt():
    payload = valid_payload()
    payload["source_citations"] = [{"claim": "先理解", "excerpt": "不在正文中的引文"}]

    with pytest.raises(DerivationValidationError, match="excerpt"):
        validate_article_derivation(payload, source_text="无关正文")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", 42),
        ("key_points", "先理解"),
        ("evergreen_score", True),
        ("reading_priority", 2.5),
        ("source_citations", []),
    ],
)
def test_derivation_rejects_invalid_field_types(field: str, value: object):
    payload = valid_payload()
    payload[field] = value

    with pytest.raises(DerivationValidationError, match=field):
        validate_article_derivation(payload, source_text="先理解，再练习")


@pytest.mark.parametrize("field", ["evergreen_score", "reading_priority"])
@pytest.mark.parametrize("value", [-1, 6])
def test_derivation_rejects_scores_outside_zero_to_five(field: str, value: int):
    payload = valid_payload()
    payload[field] = value

    with pytest.raises(DerivationValidationError, match=field):
        validate_article_derivation(payload, source_text="先理解，再练习")


def test_derivation_rejects_invalid_content_type():
    payload = valid_payload()
    payload["content_type"] = "essay"

    with pytest.raises(DerivationValidationError, match="content_type"):
        validate_article_derivation(payload, source_text="先理解，再练习")


@pytest.mark.parametrize(
    ("field", "count"),
    [("tags", 9), ("topics", 6), ("key_points", 11)],
)
def test_derivation_rejects_collection_above_limit(field: str, count: int):
    payload = valid_payload()
    if field == "key_points":
        payload[field] = [f"point {index}" for index in range(count)]
    else:
        payload[field] = [{"name": f"item {index}", "confidence": 0.5} for index in range(count)]

    with pytest.raises(DerivationValidationError, match=field):
        validate_article_derivation(payload, source_text="先理解，再练习")


@pytest.mark.parametrize("field", ["topics", "tags"])
@pytest.mark.parametrize("confidence", [-0.1, 1.1, True])
def test_derivation_rejects_invalid_confidence(field: str, confidence: object):
    payload = valid_payload()
    payload[field] = [{"name": "学习", "confidence": confidence}]

    with pytest.raises(DerivationValidationError, match="confidence"):
        validate_article_derivation(payload, source_text="先理解，再练习")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unexpected", "value"),
        ("tags", [{"name": "方法", "confidence": 0.8, "extra": 1}]),
        ("source_citations", [{"claim": "先理解", "excerpt": "先理解", "extra": 1}]),
    ],
)
def test_derivation_rejects_unknown_fields(field: str, value: object):
    payload = valid_payload()
    payload[field] = value

    with pytest.raises(DerivationValidationError, match="unknown"):
        validate_article_derivation(payload, source_text="先理解，再练习")
