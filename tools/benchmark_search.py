"""Compare Chinese lexical-search strategies on a labeled acceptance set."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import tempfile
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Evaluation:
    recall_at_10: float
    mrr: float
    p95_ms: float
    index_bytes: int = 0
    unsupported_query_count: int = 0
    invalid_query_count: int = 0


def evaluate(
    *,
    expected: dict[str, set[str]],
    actual: dict[str, list[str]],
    elapsed_ms: list[float],
    index_bytes: int = 0,
    unsupported_queries: Iterable[str] = (),
) -> Evaluation:
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    invalid = 0
    for query, relevant in expected.items():
        if not relevant:
            invalid += 1
            continue
        results = list(dict.fromkeys(actual.get(query, [])))[:10]
        recalls.append(len(relevant.intersection(results)) / len(relevant))
        reciprocal_ranks.append(
            next((1.0 / rank for rank, item in enumerate(results, 1) if item in relevant), 0.0)
        )
    ordered = sorted(elapsed_ms)
    p95 = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)] if ordered else 0.0
    count = len(recalls)
    return Evaluation(
        recall_at_10=sum(recalls) / count if count else 0.0,
        mrr=sum(reciprocal_ranks) / count if count else 0.0,
        p95_ms=p95,
        index_bytes=index_bytes,
        unsupported_query_count=len(list(unsupported_queries)),
        invalid_query_count=invalid,
    )


def sqlite_supports_trigram() -> bool:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text, tokenize='trigram')")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()


def _identity(record: dict, path: Path, line_number: int) -> str:
    if record.get("identity"):
        return str(record["identity"])
    url = str(record.get("url", ""))
    if "/answers/" in url:
        return f"zhihu:answer:{url.split('/answers/', 1)[1].split('?', 1)[0].split('/', 1)[0]}"
    return f"{path.stem}:{record.get('id', line_number)}"


def _load_corpus(raw_dir: Path) -> list[tuple[str, str, str]]:
    documents = []
    for path in sorted(raw_dir.glob("*.jsonl")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            title = str(record.get("title", ""))
            content = str(record.get("content", record.get("text", "")))
            documents.append((_identity(record, path, line_number), title, content))
    return documents


def _jieba_tokens(text: str) -> str:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
        import jieba

    return " ".join(token.strip() for token in jieba.cut_for_search(text) if token.strip())


def _fts_phrase(value: str) -> str:
    """Encode user text as one FTS5 phrase, never as MATCH syntax."""
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _run_strategy(
    strategy: str,
    documents: list[tuple[str, str, str]],
    queries: list[dict],
    database_path: Path,
) -> dict:
    connection = sqlite3.connect(database_path)
    unsupported: list[str] = []
    fallback_count = 0
    actual: dict[str, list[str]] = {}
    elapsed: list[float] = []
    try:
        tokenizer = "trigram" if strategy == "trigram" else "unicode61"
        connection.execute(f"CREATE VIRTUAL TABLE search USING fts5(identity UNINDEXED, text, tokenize='{tokenizer}')")
        for identity, title, content in documents:
            text = f"{title} {content}"
            if strategy == "jieba":
                text = _jieba_tokens(text)
            connection.execute("INSERT INTO search(identity, text) VALUES (?, ?)", (identity, text))
        connection.commit()
        for item in queries:
            query = item["query"]
            use_fallback = strategy == "trigram" and len(query.replace(" ", "")) < 3
            expression = _fts_phrase(query)
            if strategy == "jieba":
                tokens = _jieba_tokens(query).split()
                if not tokens:
                    unsupported.append(query)
                    actual[query] = []
                    continue
                expression = " OR ".join(_fts_phrase(token) for token in tokens)
            # One untimed warm-up reduces first-query connection/page-cache noise.
            if use_fallback:
                escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                sql = "SELECT identity FROM search WHERE text LIKE ? ESCAPE '\\' LIMIT 10"
                parameters = (f"%{escaped}%",)
                fallback_count += 1
            else:
                sql = "SELECT identity FROM search WHERE search MATCH ? ORDER BY bm25(search) LIMIT 10"
                parameters = (expression,)
            try:
                connection.execute(sql, parameters).fetchall()
                rows = []
                for _ in range(3):
                    started = time.perf_counter_ns()
                    rows = connection.execute(sql, parameters).fetchall()
                    elapsed.append((time.perf_counter_ns() - started) / 1_000_000)
                actual[query] = [row[0] for row in rows]
            except sqlite3.OperationalError:
                unsupported.append(query)
                actual[query] = []
    finally:
        connection.close()
    metrics = evaluate(
        expected={item["query"]: set(item["expected_ids"]) for item in queries},
        actual=actual,
        elapsed_ms=elapsed,
        index_bytes=database_path.stat().st_size,
        unsupported_queries=unsupported,
    )
    return {"status": "ok", "fallback_query_count": fallback_count, **asdict(metrics)}


def benchmark(*, raw_dir: str | Path, queries_path: str | Path, output_path: str | Path) -> dict:
    raw_dir = Path(raw_dir)
    queries_path = Path(queries_path)
    output_path = Path(output_path)
    documents = _load_corpus(raw_dir)
    queries = [
        item
        for item in json.loads(queries_path.read_text(encoding="utf-8"))
        if item.get("subset", "lexical") == "lexical"
    ]
    digest = hashlib.sha256()
    for identity, title, content in sorted(documents):
        digest.update(json.dumps([identity, title, content], ensure_ascii=False).encode("utf-8"))
    strategies: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="pkb-search-") as temporary:
        temporary_path = Path(temporary)
        if sqlite_supports_trigram():
            strategies["trigram"] = _run_strategy("trigram", documents, queries, temporary_path / "trigram.db")
        else:
            strategies["trigram"] = {"status": "unsupported", "unsupported_query_count": len(queries)}
        try:
            strategies["jieba"] = _run_strategy("jieba", documents, queries, temporary_path / "jieba.db")
        except ImportError:
            strategies["jieba"] = {"status": "unsupported", "unsupported_query_count": len(queries)}
    result = {
        "corpus": {"document_count": len(documents), "query_count": len(queries), "sha256": digest.hexdigest()},
        "sqlite": {"version": sqlite3.sqlite_version, "trigram_supported": sqlite_supports_trigram()},
        "strategies": strategies,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = benchmark(raw_dir=args.raw_dir, queries_path=args.queries, output_path=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
