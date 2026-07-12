import json
import sqlite3

import pytest

from tools.benchmark_search import (
    benchmark,
    evaluate,
    sqlite_supports_trigram,
)


def test_evaluate_calculates_macro_metrics_and_nearest_rank_p95():
    result = evaluate(
        expected={"q1": {"a", "c"}, "q2": {"z"}},
        actual={"q1": ["a", "b", "c"], "q2": ["x", "z"]},
        elapsed_ms=[1.0, 9.0],
        index_bytes=123,
        unsupported_queries=["q3"],
    )

    assert result.recall_at_10 == pytest.approx(1.0)
    assert result.mrr == pytest.approx(0.75)
    assert result.p95_ms == 9.0
    assert result.index_bytes == 123
    assert result.unsupported_query_count == 1


def test_evaluate_counts_missing_results_as_zero_recall():
    result = evaluate(
        expected={"found": {"a"}, "missing": {"b"}},
        actual={"found": ["a"]},
        elapsed_ms=[2.0],
    )

    assert result.recall_at_10 == pytest.approx(0.5)
    assert result.mrr == pytest.approx(0.5)


def test_evaluate_deduplicates_results_and_rejects_empty_relevance_labels():
    result = evaluate(
        expected={"q": {"b"}, "invalid": set()},
        actual={"q": ["a", "a", "b"], "invalid": ["x"]},
        elapsed_ms=[1.0, 1.0],
    )

    assert result.mrr == pytest.approx(0.5)
    assert result.invalid_query_count == 1


def test_sqlite_trigram_capability_probe_matches_runtime():
    supported = sqlite_supports_trigram()

    connection = sqlite3.connect(":memory:")
    try:
        if supported:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text, tokenize='trigram')")
        else:
            with pytest.raises(sqlite3.OperationalError):
                connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text, tokenize='trigram')")
    finally:
        connection.close()


def test_fixture_benchmark_compares_same_corpus_and_writes_json(tmp_path):
    output = tmp_path / "result.json"
    result = benchmark(
        raw_dir="tests/fixtures/knowledge",
        queries_path="tests/fixtures/search_queries.json",
        output_path=output,
    )

    assert result["corpus"]["document_count"] >= 10
    assert result["corpus"]["query_count"] == 30
    assert result["sqlite"]["version"] == sqlite3.sqlite_version
    assert set(result["strategies"]) == {"trigram", "jieba"}
    assert result["strategies"]["jieba"]["unsupported_query_count"] == 0
    if result["sqlite"]["trigram_supported"]:
        assert result["strategies"]["trigram"]["unsupported_query_count"] == 0
        assert result["strategies"]["trigram"]["fallback_query_count"] == 5
    else:
        assert result["strategies"]["trigram"]["status"] == "unsupported"
    assert json.loads(output.read_text(encoding="utf-8")) == result
