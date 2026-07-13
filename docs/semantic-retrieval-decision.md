# Semantic Retrieval Decision

Status: evaluated offline. Production semantic retrieval is not approved.

## Decision

`decision: reject`

Task 26 must be skipped. The deterministic fake embedding run proves that the
benchmark, cache, metric, latency, cost, and reciprocal-rank-fusion paths work;
it is not evidence that a real embedding model understands this archive. An
adoption decision requires both the mechanical quality gates and qualified
evidence from a real model on a frozen, human-reviewed, representative query
set. This run has only the latter's public structural fixture, so
`evidence_qualified` is false even though its mechanical metrics pass.

## Reproduction

Run from the repository root:

```powershell
python tools/benchmark_semantic.py `
  --corpus tests/fixtures/knowledge/benchmark-corpus.jsonl `
  --queries tests/fixtures/search_queries.json `
  --output data/state/semantic-benchmark-fixture.json `
  --cache-root data/index/task25-run
```

The vector cache is stored only below ignored `data/index`. Its filename and
validated payload bind the model name, dimensions, and corpus hash. The JSON
report under ignored `data/state` can be regenerated and contains no API key.

## Frozen Fixture

- Corpus: 11 public synthetic documents
- Corpus SHA-256: `c4ed9ab9a81b99cd0a6ddc8051ee9995a00d5b73a1320824aa3c6630e1b78161`
- Queries: 55 human-reviewed fixture labels (33 lexical, 22 semantic-only)
- Query/relevance-set SHA-256: `acdaefce4b61a639d5a39f968b9abff2be69f644547770312ad1114b5dc8b74a`
- Provider/model: deterministic fake / `fake-hash-v1`
- Dimensions: 64
- RRF: equal-weight lexical and semantic ranks, constant `k=60`
- Candidate depth: 20 per input ranking; final evaluation depth: 10
- Text projection version: `title-content-v1`
- Query vectors cached: no (each latency measurement includes one embedding call)
- Cache state for the recorded run: cold
- Runtime: Python 3.12.10, jieba 0.42.1, Windows 11 build 22631

The RRF constant and candidate depth were frozen before this run and were not
tuned against these relevance labels. A future qualified evaluation must keep
them frozen or use separate tuning and final acceptance sets.

The semantic-only labels are paraphrases or concept descriptions based solely
on the committed public fixture. They contain no titles, body text, IDs, or
queries from the private archive.

## Recorded Result

The following values are from the cold-cache fixture run on 2026-07-13. Latency
is local wall-clock time and is expected to vary by machine; the hashes and
quality metrics are deterministic.

| Strategy | Subset | recall@10 | MRR | p95 ms |
|---|---|---:|---:|---:|
| lexical | lexical | 0.9697 | 0.9697 | 0.0475 |
| lexical | semantic | 0.5455 | 0.4432 | 0.0975 |
| semantic fake | lexical | 0.9091 | 0.2738 | 0.0995 |
| semantic fake | semantic | 0.9545 | 0.2864 | 0.0979 |
| hybrid RRF | lexical | 1.0000 | 0.9495 | 0.1468 |
| hybrid RRF | semantic | 1.0000 | 0.5441 | 0.1946 |
| hybrid RRF | overall | 1.0000 | 0.7873 | 0.1919 |

Mechanical gate result:

- semantic-query recall gain: `+0.4545` (required at least `+0.10`);
- lexical-query recall loss: `-0.0303`, meaning a gain (allowed loss at most
  `0.02`);
- hybrid local overall p95: `0.1919 ms` (required below `500 ms`);
- mechanical metric gate: pass;
- qualified evidence: false;
- final gate: reject.

The apparent recall is inflated by the deliberately tiny 11-document corpus:
recall@10 can return nearly the whole corpus, and RRF combines two broad
candidate lists. The fake vectors are SHA-256-derived and have no learned
semantic meaning. Their high recall and low latency therefore validate only
the harness and expose why the evidence qualification gate is necessary; they
must not be cited as expected production quality.

## Cost Metadata

The cold run processed 859 fake character units (corpus plus queries) across 56
embedding calls (one corpus batch and one latency-bearing call per query). It
made no network calls and incurred no monetary cost. `estimated_usd` is therefore
unknown/null rather than a fabricated zero-dollar model price. For an
OpenAI-compatible endpoint, the tool reads the API key only from
`PKB_EMBEDDING_API_KEY`, records provider-reported input tokens when available,
and can calculate an estimate only when an explicit input-token price is
provided.

No private content is sent by default: the fake provider is local, and a remote
request is possible only when the operator explicitly supplies `--endpoint`
and the API-key environment variable. Before doing that with `data/raw`, the
operator must separately authorize disclosure to that provider and review its
retention policy. Raw text, query text, vectors, provider responses, and detailed
reports remain under ignored `data/`; only aggregate non-private fixture values
belong in this document.

## What Would Change the Decision

Reconsider only after selecting a real embedding endpoint and freezing a
representative, human-reviewed private query set with separate lexical and
semantic intents. Run the tool with the real model and a deliberate qualified
evidence declaration. Production adoption still requires all three mechanical
gates: at least `+0.10` semantic recall@10, no more than `0.02` lexical recall
loss, and local p95 below `500 ms`. Until then, production search remains
lexical and no vector projection is added.
