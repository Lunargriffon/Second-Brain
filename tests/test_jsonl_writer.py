import json

import pytest

from pkb.jsonl import JsonlWriter
from pkb.schema import SchemaValidationError
from tests.test_schema_validation import valid_article


def test_jsonl_writer_creates_parent_directory_and_writes_one_article_per_line(tmp_path):
    output = tmp_path / "data" / "raw" / "zhihu.jsonl"
    article = valid_article()

    writer = JsonlWriter(output)
    writer.write(article)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == article


def test_jsonl_writer_validates_article_before_writing(tmp_path):
    output = tmp_path / "data" / "raw" / "zhihu.jsonl"
    article = valid_article()
    del article["content"]

    writer = JsonlWriter(output)

    with pytest.raises(SchemaValidationError):
        writer.write(article)

    assert not output.exists()
