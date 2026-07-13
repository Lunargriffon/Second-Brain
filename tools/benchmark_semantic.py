"""Evaluate lexical versus semantic/hybrid retrieval without changing production search."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence

from pkb.semantic import (
    DeterministicFakeEmbeddings,
    LexicalBenchmarkAdapter,
    OpenAICompatibleEmbeddings,
    SemanticBenchmarkAdapter,
    SemanticCache,
    TEXT_PROJECTION_VERSION,
    evaluate_rankings,
    materially_improves,
    metrics_dict,
    reciprocal_rank_fusion,
    stable_content_hash,
    timed_rank,
)


def _load_jsonl(path: str | Path) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        documents.append(
            {
                "identity": str(row["identity"]),
                "title": str(row.get("title", "")),
                "content": str(row.get("content", "")),
            }
        )
    identities = [row["identity"] for row in documents]
    if len(identities) != len(set(identities)):
        raise ValueError("benchmark corpus identities must be unique")
    return sorted(documents, key=lambda row: row["identity"])


def _load_queries(path: str | Path) -> list[dict[str, object]]:
    queries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(queries, list):
        raise ValueError("benchmark query set must be a list")
    for item in queries:
        if item.get("subset") not in {"lexical", "semantic"}:
            raise ValueError("each query must be labeled lexical or semantic")
        if not item.get("expected_ids"):
            raise ValueError("each query must have relevant identities")
    return queries


def _split_metrics(
    queries: Sequence[Mapping[str, object]],
    rankings: Mapping[str, Sequence[str]],
    elapsed: Mapping[str, float],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for subset in ("lexical", "semantic"):
        selected = [item for item in queries if item["subset"] == subset]
        result[subset] = metrics_dict(evaluate_rankings(selected, rankings, elapsed))
    result["overall"] = metrics_dict(evaluate_rankings(queries, rankings, elapsed))
    return result


def benchmark(
    *,
    corpus_path: str | Path,
    queries_path: str | Path,
    output_path: str | Path,
    cache_root: str | Path = "data/index",
    provider=None,
    rrf_constant: int = 60,
    candidate_depth: int = 20,
    qualified_evidence: bool = False,
    cost_per_million_input_tokens: float | None = None,
) -> dict[str, object]:
    if candidate_depth < 10:
        raise ValueError("candidate_depth must be at least 10")
    documents = _load_jsonl(corpus_path)
    queries = _load_queries(queries_path)
    corpus_identities = {document["identity"] for document in documents}
    query_texts_seen: set[str] = set()
    for item in queries:
        query = str(item["query"]).strip()
        if not query:
            raise ValueError("benchmark queries must not be empty")
        if query in query_texts_seen:
            raise ValueError("benchmark queries must be unique")
        query_texts_seen.add(query)
        unknown = {str(value) for value in item["expected_ids"]} - corpus_identities
        if unknown:
            raise ValueError("relevance label points outside benchmark corpus")
    provider = provider or DeterministicFakeEmbeddings()
    initial_input_tokens = int(getattr(provider, "input_tokens", 0))
    initial_request_count = int(getattr(provider, "request_count", 0))
    corpus_hash = stable_content_hash(documents)
    query_hash = stable_content_hash(queries)

    cache = SemanticCache(cache_root)
    vectors = cache.load(
        model=provider.model_name,
        dimensions=provider.dimensions,
        corpus_hash=corpus_hash,
    )
    if vectors is not None and set(vectors) != corpus_identities:
        vectors = None
    cache_hit = vectors is not None
    if vectors is None:
        embedded = provider.embed(
            [f"{document['title']}\n{document['content']}" for document in documents]
        )
        vectors = {
            document["identity"]: vector for document, vector in zip(documents, embedded)
        }
        cache.save(
            model=provider.model_name,
            dimensions=provider.dimensions,
            corpus_hash=corpus_hash,
            vectors=vectors,
        )

    lexical = LexicalBenchmarkAdapter(documents)
    semantic = SemanticBenchmarkAdapter(vectors)
    query_texts = [str(item["query"]) for item in queries]
    lexical_rankings: dict[str, list[str]] = {}
    semantic_rankings: dict[str, list[str]] = {}
    hybrid_rankings: dict[str, list[str]] = {}
    lexical_elapsed: dict[str, float] = {}
    semantic_elapsed: dict[str, float] = {}
    hybrid_elapsed: dict[str, float] = {}
    for item, query in zip(queries, query_texts):
        started = time.perf_counter_ns()
        query_vector = provider.embed([query])[0]
        embedding_ms = (time.perf_counter_ns() - started) / 1_000_000
        query = str(item["query"])
        lexical_rankings[query], lexical_ms = timed_rank(
            lexical.rank, query, limit=candidate_depth
        )
        semantic_rankings[query], semantic_ms = timed_rank(
            semantic.rank, query_vector, limit=candidate_depth
        )
        started = time.perf_counter_ns()
        hybrid_rankings[query] = reciprocal_rank_fusion(
            [lexical_rankings[query], semantic_rankings[query]],
            constant=rrf_constant,
            limit=10,
        )
        fusion_ms = (time.perf_counter_ns() - started) / 1_000_000
        lexical_elapsed[query] = lexical_ms
        semantic_elapsed[query] = embedding_ms + semantic_ms
        hybrid_elapsed[query] = lexical_ms + embedding_ms + semantic_ms + fusion_ms

    metrics = {
        "lexical": _split_metrics(queries, lexical_rankings, lexical_elapsed),
        "semantic": _split_metrics(queries, semantic_rankings, semantic_elapsed),
        "hybrid": _split_metrics(queries, hybrid_rankings, hybrid_elapsed),
    }
    semantic_gain = (
        float(metrics["hybrid"]["semantic"]["recall_at_10"])
        - float(metrics["lexical"]["semantic"]["recall_at_10"])
    )
    lexical_loss = (
        float(metrics["lexical"]["lexical"]["recall_at_10"])
        - float(metrics["hybrid"]["lexical"]["recall_at_10"])
    )
    latency_ok = float(metrics["hybrid"]["overall"]["p95_ms"]) < 500.0
    metric_pass = (
        materially_improves(
            lexical_recall=float(metrics["lexical"]["semantic"]["recall_at_10"]),
            hybrid_recall=float(metrics["hybrid"]["semantic"]["recall_at_10"]),
            minimum_gain=0.10,
        )
        and lexical_loss <= 0.02
        and latency_ok
    )
    evidence_qualified = bool(provider.is_representative and qualified_evidence)
    input_tokens = int(getattr(provider, "input_tokens", 0)) - initial_input_tokens
    request_count = int(getattr(provider, "request_count", 0)) - initial_request_count
    estimated_cost = (
        input_tokens * cost_per_million_input_tokens / 1_000_000
        if cost_per_million_input_tokens is not None
        else None
    )
    subset_counts = {
        subset: sum(item["subset"] == subset for item in queries)
        for subset in ("lexical", "semantic")
    }
    result: dict[str, object] = {
        "corpus": {
            "document_count": len(documents),
            "sha256": corpus_hash,
        },
        "query_set": {
            "query_count": len(queries),
            "sha256": query_hash,
            "subsets": subset_counts,
        },
        "configuration": {
            "provider": type(provider).__name__,
            "model": provider.model_name,
            "dimensions": provider.dimensions,
            "representative_model": bool(provider.is_representative),
            "rrf_constant": rrf_constant,
            "candidate_depth": candidate_depth,
            "text_projection_version": TEXT_PROJECTION_VERSION,
            "query_vectors_cached": False,
            "cache_hit": cache_hit,
            "runtime": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "jieba": importlib.metadata.version("jieba"),
            },
        },
        "metrics": metrics,
        "cost": {
            "input_tokens_or_fake_characters": input_tokens,
            "embedding_request_count": request_count,
            "price_per_million_input_tokens_usd": cost_per_million_input_tokens,
            "estimated_usd": estimated_cost,
        },
        "gate": {
            "minimum_semantic_recall_gain": 0.10,
            "maximum_lexical_recall_loss": 0.02,
            "maximum_local_p95_ms": 500.0,
            "semantic_recall_gain": semantic_gain,
            "lexical_recall_loss": lexical_loss,
            "latency_ok": latency_ok,
            "metric_pass": metric_pass,
            "evidence_qualified": evidence_qualified,
            "decision": "adopt" if metric_pass and evidence_qualified else "reject",
        },
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache-root", default="data/index")
    parser.add_argument("--endpoint")
    parser.add_argument("--model", default="fake-hash-v1")
    parser.add_argument("--dimensions", type=int, default=64)
    parser.add_argument("--rrf-constant", type=int, default=60)
    parser.add_argument("--candidate-depth", type=int, default=20)
    parser.add_argument("--qualified-evidence", action="store_true")
    parser.add_argument("--cost-per-million-input-tokens", type=float)
    args = parser.parse_args()
    if args.endpoint:
        api_key = os.environ.get("PKB_EMBEDDING_API_KEY")
        if not api_key:
            parser.error("PKB_EMBEDDING_API_KEY is required with --endpoint")
        provider = OpenAICompatibleEmbeddings(
            base_url=args.endpoint,
            api_key=api_key,
            model=args.model,
            dimensions=args.dimensions,
        )
    else:
        provider = DeterministicFakeEmbeddings(dimensions=args.dimensions)
    result = benchmark(
        corpus_path=args.corpus,
        queries_path=args.queries,
        output_path=args.output,
        cache_root=args.cache_root,
        provider=provider,
        rrf_constant=args.rrf_constant,
        candidate_depth=args.candidate_depth,
        qualified_evidence=args.qualified_evidence,
        cost_per_million_input_tokens=args.cost_per_million_input_tokens,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
