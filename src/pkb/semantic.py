"""Offline adapters for evaluating semantic retrieval before production use."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import OpenerDirector, Request, build_opener

TEXT_PROJECTION_VERSION = "title-content-v1"


def materially_improves(
    *, lexical_recall: float, hybrid_recall: float, minimum_gain: float = 0.10
) -> bool:
    """Return whether hybrid recall clears the absolute-gain gate."""
    return hybrid_recall - lexical_recall >= minimum_gain


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[str]], *, constant: int = 60, limit: int = 10
) -> list[str]:
    """Fuse ranked identities; lexical/semantic inputs receive equal weight."""
    if constant < 0:
        raise ValueError("constant must be non-negative")
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, identity in enumerate(dict.fromkeys(ranking), 1):
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (constant + rank)
    return [
        identity
        for identity, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def cosine_rank(
    query: Sequence[float], documents: Mapping[str, Sequence[float]], *, limit: int = 10
) -> list[str]:
    """Rank vectors by cosine similarity with stable identity tie-breaking."""
    query_norm = math.sqrt(sum(value * value for value in query))

    def similarity(vector: Sequence[float]) -> float:
        if len(vector) != len(query):
            raise ValueError("embedding dimensions must match")
        norm = math.sqrt(sum(value * value for value in vector))
        if not query_norm or not norm:
            return 0.0
        return sum(left * right for left, right in zip(query, vector)) / (query_norm * norm)

    return [
        identity
        for identity, _ in sorted(
            ((identity, similarity(vector)) for identity, vector in documents.items()),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    ]


class DeterministicFakeEmbeddings:
    """Stable offline hash embeddings for exercising the benchmark harness only."""

    model_name = "fake-hash-v1"
    is_representative = False

    def __init__(self, *, dimensions: int = 64) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions
        self.input_tokens = 0
        self.request_count = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.request_count += 1
        vectors: list[list[float]] = []
        for text in texts:
            self.input_tokens += len(text)
            values: list[float] = []
            counter = 0
            while len(values) < self.dimensions:
                block = hashlib.sha256(
                    f"{counter}\0{text}".encode("utf-8")
                ).digest()
                values.extend((byte - 127.5) / 127.5 for byte in block)
                counter += 1
            vector = values[: self.dimensions]
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class EmbeddingProviderError(RuntimeError):
    """An embedding request failed without exposing credentials or response bodies."""


class OpenAICompatibleEmbeddings:
    """Minimal batch client for an OpenAI-compatible ``/embeddings`` endpoint."""

    is_representative = True

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        timeout_seconds: float = 60,
        opener: OpenerDirector | object | None = None,
    ) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model_name = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self._opener = opener or build_opener()
        self.input_tokens = 0
        self.request_count = 0

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(base_url={self.base_url!r}, "
            f"model={self.model_name!r}, dimensions={self.dimensions!r})"
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.request_count += 1
        body = json.dumps(
            {"model": self.model_name, "input": list(texts), "dimensions": self.dimensions}
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/embeddings",
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                envelope = json.load(response)
        except HTTPError as exc:
            raise EmbeddingProviderError(f"embedding endpoint failed (HTTP {exc.code})") from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise EmbeddingProviderError("embedding endpoint request failed") from None
        try:
            rows = sorted(envelope["data"], key=lambda row: int(row["index"]))
            vectors = [[float(value) for value in row["embedding"]] for row in rows]
            usage = envelope.get("usage", {})
            self.input_tokens += int(usage.get("prompt_tokens", 0))
        except (KeyError, TypeError, ValueError):
            raise EmbeddingProviderError("embedding endpoint returned an invalid response") from None
        if len(vectors) != len(texts) or any(len(vector) != self.dimensions for vector in vectors):
            raise EmbeddingProviderError("embedding endpoint returned an invalid response")
        return vectors


class SemanticCache:
    """JSON vector cache constrained to the ignored ``data/index`` tree."""

    def __init__(self, root: str | Path = "data/index") -> None:
        self.root = Path(root)
        parts = tuple(part.casefold() for part in self.root.resolve().parts)
        inside_data_index = any(
            parts[index : index + 2] == ("data", "index")
            for index in range(max(0, len(parts) - 1))
        )
        if not inside_data_index:
            raise ValueError("semantic cache must be stored under data/index")

    def path(
        self,
        *,
        model: str,
        dimensions: int,
        corpus_hash: str,
        projection_version: str = TEXT_PROJECTION_VERSION,
    ) -> Path:
        safe_model = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"
        fingerprint = hashlib.sha256(
            f"{model}\0{dimensions}\0{corpus_hash}\0{projection_version}".encode("utf-8")
        ).hexdigest()[:16]
        return self.root / f"semantic-{safe_model}-{dimensions}-{corpus_hash[:12]}-{fingerprint}.json"

    def load(
        self,
        *,
        model: str,
        dimensions: int,
        corpus_hash: str,
        projection_version: str = TEXT_PROJECTION_VERSION,
    ) -> dict[str, list[float]] | None:
        path = self.path(
            model=model,
            dimensions=dimensions,
            corpus_hash=corpus_hash,
            projection_version=projection_version,
        )
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload["model"] != model or payload["dimensions"] != dimensions:
                return None
            if payload["corpus_hash"] != corpus_hash:
                return None
            if payload["projection_version"] != projection_version:
                return None
            vectors = {
                str(identity): [float(value) for value in vector]
                for identity, vector in payload["vectors"].items()
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if any(len(vector) != dimensions for vector in vectors.values()):
            return None
        return vectors

    def save(
        self,
        *,
        model: str,
        dimensions: int,
        corpus_hash: str,
        vectors: Mapping[str, Sequence[float]],
        projection_version: str = TEXT_PROJECTION_VERSION,
    ) -> Path:
        path = self.path(
            model=model,
            dimensions=dimensions,
            corpus_hash=corpus_hash,
            projection_version=projection_version,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": model,
            "dimensions": dimensions,
            "corpus_hash": corpus_hash,
            "projection_version": projection_version,
            "vectors": {identity: list(vectors[identity]) for identity in sorted(vectors)},
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return path


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_10: float
    mrr: float
    p95_ms: float
    query_count: int


def evaluate_rankings(
    queries: Sequence[Mapping[str, object]],
    rankings: Mapping[str, Sequence[str]],
    elapsed_ms: Mapping[str, float],
) -> RetrievalMetrics:
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    latencies: list[float] = []
    for item in queries:
        query = str(item["query"])
        relevant = {str(identity) for identity in item["expected_ids"]}  # type: ignore[union-attr]
        if not relevant:
            raise ValueError("each benchmark query must have at least one relevant identity")
        results = list(dict.fromkeys(rankings.get(query, ())))[:10]
        recalls.append(len(relevant.intersection(results)) / len(relevant))
        reciprocal_ranks.append(
            next((1.0 / rank for rank, identity in enumerate(results, 1) if identity in relevant), 0.0)
        )
        latencies.append(float(elapsed_ms.get(query, 0.0)))
    ordered = sorted(latencies)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return RetrievalMetrics(
        recall_at_10=sum(recalls) / len(recalls) if recalls else 0.0,
        mrr=sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0,
        p95_ms=ordered[p95_index] if ordered else 0.0,
        query_count=len(recalls),
    )


class LexicalBenchmarkAdapter:
    """Small in-memory lexical candidate adapter used only by the evaluator."""

    def __init__(self, documents: Sequence[Mapping[str, str]]) -> None:
        self.documents = tuple(documents)
        self._fields = {
            document["identity"]: (
                self._tokens(document["title"]), self._tokens(document["content"])
            )
            for document in self.documents
        }

    @staticmethod
    def _tokens(text: str) -> tuple[str, ...]:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
            import jieba

        jieba.setLogLevel(logging.WARNING)
        return tuple(token.casefold() for token in jieba.cut_for_search(text) if token.strip())

    def rank(self, query: str, *, limit: int) -> list[str]:
        tokens = set(self._tokens(query))
        scored: list[tuple[float, str]] = []
        for identity, (title, content) in self._fields.items():
            title_overlap = sum(token in title for token in tokens)
            content_overlap = sum(token in content for token in tokens)
            score = title_overlap * 3.0 + content_overlap
            if score:
                scored.append((score, identity))
        return [identity for _, identity in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]


class SemanticBenchmarkAdapter:
    """Bounded brute-force cosine adapter for benchmark-sized corpora."""

    def __init__(self, vectors: Mapping[str, Sequence[float]]) -> None:
        self.vectors = vectors

    def rank(self, query_vector: Sequence[float], *, limit: int) -> list[str]:
        return cosine_rank(query_vector, self.vectors, limit=limit)


def stable_content_hash(rows: Sequence[Mapping[str, object]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True)):
        digest.update(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def timed_rank(function, *args, **kwargs) -> tuple[list[str], float]:
    started = time.perf_counter_ns()
    result = function(*args, **kwargs)
    return result, (time.perf_counter_ns() - started) / 1_000_000


def metrics_dict(metrics: RetrievalMetrics) -> dict[str, object]:
    return asdict(metrics)
