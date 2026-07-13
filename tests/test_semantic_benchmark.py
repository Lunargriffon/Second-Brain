import json
from io import BytesIO
from pathlib import Path

import pytest

from pkb.semantic import (
    DeterministicFakeEmbeddings,
    OpenAICompatibleEmbeddings,
    SemanticCache,
    cosine_rank,
    materially_improves,
    reciprocal_rank_fusion,
)
from tools.benchmark_semantic import benchmark


def test_semantic_gate_requires_material_recall_gain():
    assert materially_improves(
        lexical_recall=0.70,
        hybrid_recall=0.82,
        minimum_gain=0.10,
    )
    assert not materially_improves(
        lexical_recall=0.80,
        hybrid_recall=0.85,
        minimum_gain=0.10,
    )


def test_public_query_fixture_has_separate_human_reviewed_subsets():
    queries = json.loads(Path("tests/fixtures/search_queries.json").read_text(encoding="utf-8"))
    semantic = [item for item in queries if item["subset"] == "semantic"]

    assert len(semantic) >= 20
    assert {item["subset"] for item in queries} == {"lexical", "semantic"}
    assert all(item["expected_ids"] for item in queries)


def test_rrf_fuses_rankings_with_stable_identity_tiebreak():
    assert reciprocal_rank_fusion(
        [["b", "a"], ["a", "b"]], constant=60, limit=10
    ) == ["a", "b"]


def test_fake_embeddings_are_deterministic_and_fixed_dimension():
    provider = DeterministicFakeEmbeddings(dimensions=32)

    first = provider.embed(["相同文本", "另一个文本"])
    second = provider.embed(["相同文本", "另一个文本"])

    assert first == second
    assert all(len(vector) == 32 for vector in first)
    assert cosine_rank(first[0], {"b": first[1], "a": first[0]}) == ["a", "b"]


def test_semantic_cache_is_limited_to_data_index_and_keyed_by_projection(tmp_path):
    cache = SemanticCache(tmp_path / "data" / "index")
    first = cache.path(model="fake/model", dimensions=32, corpus_hash="abc")
    second = cache.path(model="fake/model", dimensions=64, corpus_hash="abc")
    changed_projection = cache.path(
        model="fake/model",
        dimensions=32,
        corpus_hash="abc",
        projection_version="title-content-v2",
    )

    assert first.parent == tmp_path / "data" / "index"
    assert first != second
    assert first != changed_projection
    assert "fake-model" in first.name
    with pytest.raises(ValueError, match="data/index"):
        SemanticCache(tmp_path / "data" / "state")
    with pytest.raises(ValueError, match="data/index"):
        SemanticCache(tmp_path / "metadata" / "index")
    assert SemanticCache(tmp_path / "data" / "index" / "semantic").root.name == "semantic"


class _Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _Opener:
    def __init__(self):
        self.request = None

    def open(self, request, timeout):
        self.request = request
        return _Response(
            json.dumps(
                {"data": [{"index": 0, "embedding": [1.0, 0.0]}], "usage": {"prompt_tokens": 2}}
            ).encode("utf-8")
        )


def test_openai_compatible_embedding_adapter_posts_batch_without_exposing_key():
    opener = _Opener()
    provider = OpenAICompatibleEmbeddings(
        base_url="http://localhost:11434/v1",
        api_key="top-secret",
        model="fixture-model",
        dimensions=2,
        opener=opener,
    )

    assert provider.embed(["hello"]) == [[1.0, 0.0]]
    body = json.loads(opener.request.data)
    assert opener.request.full_url == "http://localhost:11434/v1/embeddings"
    assert body == {"model": "fixture-model", "input": ["hello"], "dimensions": 2}
    assert "top-secret" not in repr(provider)


def test_fixture_benchmark_reports_subsets_cost_gate_and_rejects_fake_evidence(tmp_path):
    output = tmp_path / "semantic.json"
    cache_root = tmp_path / "data" / "index"

    provider = DeterministicFakeEmbeddings(dimensions=64)
    result = benchmark(
        corpus_path="tests/fixtures/knowledge/benchmark-corpus.jsonl",
        queries_path="tests/fixtures/search_queries.json",
        output_path=output,
        cache_root=cache_root,
        provider=provider,
    )

    assert result["configuration"]["rrf_constant"] == 60
    assert result["configuration"]["candidate_depth"] >= 10
    assert result["configuration"]["representative_model"] is False
    assert set(result["configuration"]["runtime"]) == {"python", "platform", "jieba"}
    assert set(result["metrics"]) == {"lexical", "semantic", "hybrid"}
    assert set(result["metrics"]["hybrid"]) == {"lexical", "semantic", "overall"}
    assert result["cost"]["estimated_usd"] is None
    assert result["cost"]["embedding_request_count"] == 56
    assert result["gate"]["decision"] == "reject"
    assert result["gate"]["evidence_qualified"] is False
    assert result["query_set"]["subsets"] == {"lexical": 33, "semantic": 22}
    assert output.exists()
    assert list(cache_root.glob("semantic-*.json"))


def test_benchmark_rebuilds_a_manifest_incomplete_vector_cache(tmp_path):
    cache_root = tmp_path / "data" / "index"
    cache = SemanticCache(cache_root)
    corpus = [
        json.loads(line)
        for line in Path("tests/fixtures/knowledge/benchmark-corpus.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    from pkb.semantic import stable_content_hash

    corpus_hash = stable_content_hash(sorted(corpus, key=lambda row: row["identity"]))
    cache.save(
        model="fake-hash-v1",
        dimensions=64,
        corpus_hash=corpus_hash,
        vectors={"fixture:1": [0.0] * 64},
    )
    provider = DeterministicFakeEmbeddings(dimensions=64)

    result = benchmark(
        corpus_path="tests/fixtures/knowledge/benchmark-corpus.jsonl",
        queries_path="tests/fixtures/search_queries.json",
        output_path=tmp_path / "output.json",
        cache_root=cache_root,
        provider=provider,
    )

    assert result["configuration"]["cache_hit"] is False
    assert result["cost"]["embedding_request_count"] == 56


def test_benchmark_rejects_relevance_labels_outside_frozen_corpus(tmp_path):
    queries = tmp_path / "queries.json"
    queries.write_text(
        json.dumps(
            [{"query": "missing", "expected_ids": ["private:unknown"], "subset": "semantic"}]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="outside benchmark corpus"):
        benchmark(
            corpus_path="tests/fixtures/knowledge/benchmark-corpus.jsonl",
            queries_path=queries,
            output_path=tmp_path / "output.json",
            cache_root=tmp_path / "data" / "index",
            provider=DeterministicFakeEmbeddings(dimensions=8),
        )
