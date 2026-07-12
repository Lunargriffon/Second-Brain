import pytest

from pkb.schema import SchemaValidationError, validate_article


def valid_article() -> dict:
    return {
        "schema_version": "1.0",
        "id": "zhihu_123456789",
        "source": "zhihu",
        "title": "A useful answer",
        "url": "https://www.zhihu.com/question/123/answer/456",
        "author": "Example Author",
        "content": "Useful content.",
        "created_at": "2024-01-15T08:30:00Z",
        "saved_at": "2026-06-30T12:00:00Z",
        "images": [],
    }


def test_validate_article_accepts_valid_article():
    validate_article(valid_article())


def test_validate_article_rejects_missing_required_field():
    article = valid_article()
    del article["content"]

    with pytest.raises(SchemaValidationError):
        validate_article(article)


def test_validate_article_rejects_extra_field():
    article = valid_article()
    article["summary"] = "Phase 2 data must not be stored in raw output."

    with pytest.raises(SchemaValidationError):
        validate_article(article)


def test_validate_article_rejects_invalid_iso_datetime():
    article = valid_article()
    article["created_at"] = "2024-99-99T99:99:99Z"

    with pytest.raises(SchemaValidationError):
        validate_article(article)


def test_validate_article_accepts_source_collection_metadata():
    article = valid_article()
    article["source_collection_id"] = "575638886"
    article["source_collection_title"] = "恋爱"

    validate_article(article)
