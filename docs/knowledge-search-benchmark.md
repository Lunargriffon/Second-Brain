# Chinese Lexical Search Benchmark

Status: accepted. Jieba pretokenization is the production lexical-search strategy.

## Method

Both candidates index the same normalized corpus and execute the same 30-query
acceptance set. Results are deduplicated by document identity before macro
recall@10 and MRR are calculated. Queries without a relevant-document label are
invalid and excluded from quality metrics. Latency uses one untimed warm-up and
three timed executions per query; p95 is the nearest-rank percentile.

The candidates are:

- SQLite FTS5 `trigram`, with a parameterized `LIKE` substring fallback for
  normalized queries shorter than three characters. This is not described as
  pure trigram; fallback use is reported separately.
- Jieba search-mode pretokenization stored in an FTS5 `unicode61` index.

## Deterministic fixture result

Run on SQLite 3.49.1 against 13 non-private fixture documents and 33 labeled
queries. Corpus SHA-256:
`36c9994c4be288f0940e75c3f4a0675fdbffa2335c11b71a8cc04b69f5d13eb3`.

| Strategy | recall@10 | MRR | p95 ms | Index bytes | Unsupported | Fallback |
|---|---:|---:|---:|---:|---:|---:|
| trigram + short-query fallback | 0.8788 | 0.8636 | 0.0629 | 36,864 | 0 | 5 |
| Jieba pretokenization | 0.9697 | 0.9545 | 0.0501 | 24,576 | 0 | 0 |

The acceptance set includes literal FTS syntax characters such as `C++`, `/`,
and `-`. Both candidates encode all user input as an FTS5 phrase; quotes inside
the value are doubled before parameter binding, so user text is never evaluated
as MATCH syntax.

Jieba is the provisional fixture winner. This is not yet sufficient evidence to
select the production default because the deterministic fixtures do not model
the vocabulary and relevance judgments of the private archive.

## Real-corpus acceptance result

The ignored file `data/state/search-queries-real.json` contains 40 manually
reviewed queries over 1,257 archived documents. It covers short Chinese terms,
mixed Chinese/English text, title and body intents, and literal FTS syntax
characters. Private query text, titles, and document IDs remain uncommitted.

Reproduce the private benchmark locally with:

```powershell
python tools/benchmark_search.py `
  --raw-dir data/raw `
  --queries data/state/search-queries-real.json `
  --output data/state/search-benchmark-real.json
```

Both candidates achieved all acceptance thresholds:

- macro recall@10 at least 0.90;
- MRR at least 0.75;
- p95 latency below 100 ms;
- zero unsupported and invalid acceptance queries.

| Strategy | recall@10 | MRR | p95 ms | Index bytes | Unsupported | Invalid | Fallback |
|---|---:|---:|---:|---:|---:|---:|---:|
| trigram + short-query fallback | 0.9250 | 0.7598 | 1.2081 | 53,719,040 | 0 | 0 | 5 |
| Jieba pretokenization | 0.9750 | 0.7981 | 6.9514 | 25,362,432 | 0 | 0 | 0 |

Jieba is selected because it has higher recall and MRR, uses less than half the
index space, supports every accepted short query without a separate fallback,
and remains far below the latency ceiling. The benchmark was independently
rerun before recording these aggregate metrics.

The real report stays under ignored `data/state/`; only aggregate, non-private
metrics are recorded here.

## Reproduce the fixture baseline

```powershell
python -m pip install -e ".[search]"
python -m pytest tests/test_search_benchmark.py -v
python tools/benchmark_search.py `
  --raw-dir tests/fixtures/knowledge `
  --queries tests/fixtures/search_queries.json `
  --output data/state/search-benchmark-fixture.json
```
