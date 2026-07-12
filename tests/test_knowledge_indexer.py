from __future__ import annotations

import json
from pathlib import Path

import pytest

from pkb.knowledge.indexer import IndexBuildError, KnowledgeIndexer
from pkb.knowledge.repository import KnowledgeRepository
from pkb.knowledge.search import SearchIndex


def _write(path: Path, records: list[dict[str, object] | str]) -> None:
    path.write_text(
        "\n".join(item if isinstance(item, str) else json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8-sig",
    )


def _zhihu(answer: str = "123", content: str = "先理解，再练习。") -> dict[str, object]:
    return {
        "id": f"zhihu_answer_{answer}", "title": "学习方法", "author": "甲",
        "content": content, "url": f"https://www.zhihu.com/api/v4/answers/{answer}",
        "created_at": "2025-01-01T00:00:00Z", "images": [],
    }


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    _write(raw / "zhihu-10.jsonl", [_zhihu()])
    return raw


@pytest.fixture
def repository(tmp_path: Path) -> KnowledgeRepository:
    repo = KnowledgeRepository(tmp_path / "knowledge.db")
    yield repo
    repo.close()


def test_second_index_run_is_unchanged(repository: KnowledgeRepository, raw_dir: Path) -> None:
    indexer = KnowledgeIndexer(repository)
    first = indexer.build(raw_dir)
    second = indexer.build(raw_dir)

    assert first.created == 1
    assert first.derivation_jobs_queued == 1
    assert second.created == second.updated == second.derivation_jobs_queued == 0
    assert second.unchanged == first.processed == 1
    assert second.search_projections_updated == 0


def test_routes_supported_top_level_files_and_skips_samples_and_unknowns(
    repository: KnowledgeRepository, raw_dir: Path
) -> None:
    _write(raw_dir / "zhihu-author.jsonl", [_zhihu("124")])
    _write(raw_dir / "zhihu-99.sample.jsonl", [_zhihu("125")])
    _write(raw_dir / "x-bookmarks.fieldtheory.jsonl", [{
        "id": "tweet-1", "url": "https://x.com/a/status/1", "text": "值得保存",
        "links": ["https://example.com/article"],
    }])
    _write(raw_dir / "other.jsonl", [_zhihu("126")])
    nested = raw_dir / "nested"
    nested.mkdir()
    _write(nested / "zhihu-11.jsonl", [_zhihu("127")])

    report = KnowledgeIndexer(repository).build(raw_dir)

    assert report.discovered == 5
    assert report.processed == 3
    assert report.skipped == 2
    assert repository.count_documents() == 3


def test_cross_source_link_merges_document_and_preserves_memberships(
    repository: KnowledgeRepository, raw_dir: Path
) -> None:
    _write(raw_dir / "x-bookmarks.jsonl", [{
        "id": "tweet-1", "url": "https://x.com/a/status/1", "text": "转发",
        "links": ["https://www.zhihu.com/question/9/answer/123?utm_source=x"],
    }])

    KnowledgeIndexer(repository).build(raw_dir)

    assert repository.count_documents() == 1
    document_id = repository.resolve_identity("zhihu:answer:123")
    assert document_id is not None
    assert repository.count_memberships(document_id) == 2


def test_bad_line_continues_by_default_and_strict_raises(repository: KnowledgeRepository, raw_dir: Path) -> None:
    _write(raw_dir / "zhihu-10.jsonl", [_zhihu(), "{secret bad json", _zhihu("124")])
    indexer = KnowledgeIndexer(repository)

    report = indexer.build(raw_dir)
    assert report.processed == 2
    assert report.failed == 1
    assert report.errors[0].path.endswith("zhihu-10.jsonl")
    assert report.errors[0].line == 2
    assert "secret" not in report.errors[0].error
    assert repository.count_documents() == 2

    with pytest.raises(IndexBuildError, match=r"zhihu-10\.jsonl:2"):
        indexer.build(raw_dir, strict=True)
    assert repository.count_documents() == 2


def test_strict_build_rolls_back_all_records_from_current_batch(
    repository: KnowledgeRepository, raw_dir: Path
) -> None:
    _write(raw_dir / "zhihu-10.jsonl", [_zhihu(), _zhihu("124"), "{bad"])

    with pytest.raises(IndexBuildError):
        KnowledgeIndexer(repository).build(raw_dir, strict=True)

    assert repository.count_documents() == 0
    assert repository.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert repository.connection.execute("SELECT COUNT(*) FROM documents_search_content").fetchone()[0] == 0


def test_diagnostic_uses_relative_path_and_safe_error_code(
    repository: KnowledgeRepository, raw_dir: Path
) -> None:
    _write(raw_dir / "zhihu-10.jsonl", ['{"password":"do-not-leak", bad}'])

    diagnostic = KnowledgeIndexer(repository).build(raw_dir).errors[0]

    assert diagnostic.path == "zhihu-10.jsonl"
    assert diagnostic.error == "invalid_json"
    assert "do-not-leak" not in repr(diagnostic)


def test_missing_stable_identity_is_failed(repository: KnowledgeRepository, raw_dir: Path) -> None:
    _write(raw_dir / "x-bookmarks.jsonl", [{"text": "anonymous", "links": ["https://example.com/a"]}])

    report = KnowledgeIndexer(repository).build(raw_dir)

    assert report.failed == 1
    assert repository.count_documents() == 1  # the valid Zhihu fixture only

def test_source_edit_queues_one_new_job_and_preserves_old_derivation(
    repository: KnowledgeRepository, raw_dir: Path
) -> None:
    indexer = KnowledgeIndexer(repository)
    indexer.build(raw_dir)
    document_id = repository.resolve_identity("zhihu:answer:123")
    assert document_id is not None
    old_document = repository.document(document_id)
    repository.connection.execute(
        """INSERT INTO derivations
           (id, document_id, kind, payload_json, input_hash, source_content_hash,
            normalized_content_hash, normalization_version, schema_version,
            prompt_version, status) VALUES ('old', ?, 'summary', '{}', ?, ?, ?, 1, 1, 'p1', 'done')""",
        (document_id, old_document["source_content_hash"], old_document["source_content_hash"], old_document["normalized_content_hash"]),
    )
    repository.connection.commit()
    _write(raw_dir / "zhihu-10.jsonl", [_zhihu(content="先理解，再刻意练习。")])

    changed = indexer.build(raw_dir)
    unchanged = indexer.build(raw_dir)

    assert changed.updated == 1
    assert changed.derivation_jobs_queued == 1
    assert repository.connection.execute("SELECT 1 FROM derivations WHERE id='old'").fetchone()
    current = repository.document(document_id)
    pending = repository.connection.execute(
        "SELECT * FROM jobs WHERE document_id=? AND input_hash=?", (document_id, current["source_content_hash"])
    ).fetchall()
    assert len(pending) == 1
    assert pending[0]["pipeline_version"] == "article-v1"
    assert unchanged.derivation_jobs_queued == 0


def test_normalization_only_change_marks_stale_without_job(
    repository: KnowledgeRepository, raw_dir: Path
) -> None:
    KnowledgeIndexer(repository, normalization_version=1).build(raw_dir)

    report = KnowledgeIndexer(repository, normalization_version=2).build(raw_dir)

    assert report.updated == 1
    assert report.normalization_stale == 1
    assert report.derivation_jobs_queued == 0


def test_projection_is_searchable_and_rebuilt_when_tokenizer_metadata_changes(
    repository: KnowledgeRepository, raw_dir: Path
) -> None:
    indexer = KnowledgeIndexer(repository)
    first = indexer.build(raw_dir)
    assert SearchIndex(repository).search("学习")[0].title == "学习方法"
    repository.connection.execute(
        "UPDATE documents_search_content SET tokenizer_version='obsolete'"
    )
    repository.connection.commit()

    second = indexer.build(raw_dir)

    assert first.search_projections_updated == 1
    assert second.search_projections_updated == 1


def test_report_serializes_to_json(repository: KnowledgeRepository, raw_dir: Path, tmp_path: Path) -> None:
    report = KnowledgeIndexer(repository).build(raw_dir)
    target = tmp_path / "reports" / "index.json"

    report.write(target)

    assert json.loads(target.read_text(encoding="utf-8")) == report.to_dict()
