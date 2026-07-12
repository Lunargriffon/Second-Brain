# Second Brain All Phases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the verified personal archive into a searchable, versioned, AI-assisted knowledge system with an Obsidian projection, a daily review loop, bounded relationships, optional semantic retrieval, and stable MCP access.

**Architecture:** Preserve `data/raw`, `data/images`, and `data/frozen` as evidence; build a reproducible SQLite projection with Chinese-capable FTS; store AI output as versioned JSON derivations; render deterministic Markdown into an Obsidian vault; expose domain services through CLI first and MCP after contracts stabilize. Every phase has an acceptance gate, and optional semantic/Web work is evidence-gated.

**Tech Stack:** Python 3.11+, standard-library `sqlite3`, SQLite FTS5 trigram, optional `jieba`, `pytest`, JSONL/JSON, Markdown/YAML frontmatter, `urllib` for OpenAI-compatible HTTP, optional MCP Python SDK after CLI stabilization.

**Approved design:** `docs/superpowers/specs/2026-07-11-second-brain-architecture-design.md`

---

## Execution Rules

- Run tasks in order. Do not start a later phase until the preceding phase gate passes.
- Use TDD: add one focused failing test, verify the expected failure, implement the minimum behavior, then run the focused and full suites.
- Never modify files under `data/raw`, `data/images`, or `data/frozen` during Phase 2 processing.
- Default all network/model commands to a finite `--limit`; an unbounded operation requires `--unlimited`.
- Treat generated SQLite, derivations, reports, and Vault pages as projections. User files under `vault/user` are durable human state.
- The current workspace is not a Git repository. Task 1 creates a safe repository boundary before any checkpoint commit. If the user elects to keep Git metadata elsewhere, perform the equivalent setup there and preserve the ignore rules.

## Planned File Map

```text
.gitignore                                      Personal/generated-data exclusions
pyproject.toml                                  Optional jieba and MCP dependency groups
src/pkb/cli.py                                  Command registration and thin dispatch
src/pkb/sources/base.py                         RawRecordRef and SourceAdapter protocol
src/pkb/sources/zhihu.py                        Zhihu raw normalization
src/pkb/sources/x_bookmarks.py                  X bookmark normalization
src/pkb/knowledge/models.py                     NormalizedDocument domain model
src/pkb/knowledge/urls.py                       Shared URL canonicalization and platform IDs
src/pkb/knowledge/fingerprint.py                Stable source and normalized hashes
src/pkb/knowledge/migrations.py                 SQLite schema and migrations
src/pkb/knowledge/repository.py                 Transactional database boundary
src/pkb/knowledge/search.py                     Chinese lexical index and search
src/pkb/knowledge/indexer.py                    Incremental raw-to-index coordinator
src/pkb/knowledge/reports.py                    Machine-readable run reports
src/pkb/derive/models.py                        Derivation schemas and validation
src/pkb/derive/prompts.py                       Versioned article/relation prompts
src/pkb/derive/provider.py                      OpenAI-compatible provider protocol/client
src/pkb/derive/jobs.py                          Leased job queue
src/pkb/derive/pipeline.py                      Bounded article derivation pipeline
src/pkb/derive/relations.py                     Bounded relation candidates and derivation
src/pkb/wiki/renderer.py                        Deterministic Markdown pages
src/pkb/wiki/exporter.py                        Generated-file manifest and safe export
src/pkb/review.py                               Reading state and daily queue
src/pkb/semantic.py                             Evidence-gated semantic benchmark/projection
src/pkb/interfaces/mcp.py                       Stable MCP adapter over query services
tests/fixtures/knowledge/                       Small offline multi-source fixture corpus
tests/fixtures/search_queries.json              Chinese lexical acceptance set
tests/test_*.py                                 Focused unit/integration/contract tests
tools/benchmark_search.py                       Real-corpus lexical benchmark runner
docs/knowledge-search-benchmark.md              Recorded lexical decision
docs/second-brain-operations.md                 Operator workflow and recovery guide
```

---

# Phase 0 — Repository Safety and Baseline

### Task 1: Establish a Safe Version-Control Boundary

**Files:**
- Create: `.gitignore`
- Create: `tests/test_repository_hygiene.py`

- [ ] **Step 1: Write the failing hygiene test**

```python
from pathlib import Path


def test_gitignore_excludes_personal_and_generated_data():
    text = Path(".gitignore").read_text(encoding="utf-8")
    required = {
        ".env",
        "data/raw/",
        "data/images/",
        "data/frozen/",
        "data/index/",
        "data/derived/",
        "vault/articles/",
        "vault/_index/",
        ".superpowers/",
    }
    assert required <= set(text.splitlines())
```

- [ ] **Step 2: Verify the test fails**

Run: `python -m pytest tests/test_repository_hygiene.py -v`  
Expected: FAIL because `.gitignore` does not exist or lacks required entries.

- [ ] **Step 3: Add explicit exclusions**

```gitignore
.env
.env.*
!.env.example
__pycache__/
*.py[cod]
.pytest_cache/
.pytest-tmp/
.superpowers/

data/raw/
data/images/
data/frozen/
data/index/
data/derived/
data/state/*.log

vault/articles/
vault/_index/
vault/sources/
vault/daily/
vault/attachments/
!vault/user/.gitkeep
```

- [ ] **Step 4: Verify hygiene and existing behavior**

Run: `python -m pytest tests/test_repository_hygiene.py -v && python -m pytest -q`  
Expected: hygiene test passes and the existing 63-test baseline plus the new test passes.

- [ ] **Step 5: Initialize and inspect Git without staging personal data**

Run:

```powershell
git init
git status --short --ignored
git check-ignore .env data/raw/zhihu-575638886.jsonl data/images/zhihu/knowledge
```

Expected: each sensitive path is reported as ignored. Inspect `git status --short` before staging.

- [ ] **Step 6: Commit the safety boundary**

```powershell
git add .gitignore tests/test_repository_hygiene.py
git commit -m "chore: establish safe repository boundary"
```

### Task 2: Add Package Boundaries Without Moving Stable Acquisition Code

**Files:**
- Create: `src/pkb/sources/__init__.py`
- Create: `src/pkb/knowledge/__init__.py`
- Create: `src/pkb/derive/__init__.py`
- Create: `src/pkb/wiki/__init__.py`
- Create: `src/pkb/interfaces/__init__.py`
- Test: `tests/test_package_boundaries.py`

- [ ] **Step 1: Write the failing import test**

```python
def test_phase_two_packages_are_importable():
    import pkb.derive
    import pkb.interfaces
    import pkb.knowledge
    import pkb.sources
    import pkb.wiki
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_package_boundaries.py -v`  
Expected: FAIL on the first missing package.

- [ ] **Step 3: Create empty package initializers**

Each new `__init__.py` contains:

```python
"""Focused Phase 2 package; public APIs are exported explicitly by modules."""
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_package_boundaries.py -v && python -m pytest -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/sources src/pkb/knowledge src/pkb/derive src/pkb/wiki src/pkb/interfaces tests/test_package_boundaries.py
git commit -m "chore: add phase two package boundaries"
```

---

# Phase 1 — Searchable Knowledge Core

### Task 3: Define the Normalized Document Contract

**Files:**
- Create: `src/pkb/sources/base.py`
- Create: `src/pkb/knowledge/models.py`
- Test: `tests/test_knowledge_models.py`

- [ ] **Step 1: Write the failing value-object test**

```python
from pathlib import Path

from pkb.knowledge.models import NormalizedDocument, SourceMembership


def test_normalized_document_separates_identity_from_membership():
    membership = SourceMembership(
        source="zhihu",
        source_item_id="answer:1",
        collection_id="10",
        source_url="https://www.zhihu.com/question/2/answer/1",
        raw_path=Path("data/raw/zhihu-10.jsonl"),
        raw_line=3,
    )
    document = NormalizedDocument(
        identity_key="zhihu:answer:1",
        canonical_url=membership.source_url,
        title="标题",
        author="作者",
        plain_content="正文",
        media_urls=("https://pic.zhimg.com/a.jpg",),
        source_created_at="2025-01-01T00:00:00Z",
        membership=membership,
    )
    assert document.membership.collection_id == "10"
    assert document.identity_key == "zhihu:answer:1"
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_knowledge_models.py -v`  
Expected: FAIL because the models do not exist.

- [ ] **Step 3: Implement immutable contracts**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceMembership:
    source: str
    source_item_id: str
    collection_id: str | None
    source_url: str
    raw_path: Path
    raw_line: int


@dataclass(frozen=True)
class NormalizedDocument:
    identity_key: str
    canonical_url: str
    title: str
    author: str
    plain_content: str
    media_urls: tuple[str, ...]
    source_created_at: str | None
    membership: SourceMembership
```

In `sources/base.py`, define:

```python
from pathlib import Path
from typing import Mapping, Protocol

from pkb.knowledge.models import NormalizedDocument


class SourceAdapter(Protocol):
    source_name: str

    def normalize(
        self, record: Mapping[str, object], *, raw_path: Path, raw_line: int
    ) -> NormalizedDocument: ...
```

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_knowledge_models.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/sources/base.py src/pkb/knowledge/models.py tests/test_knowledge_models.py
git commit -m "feat: define normalized knowledge contracts"
```

### Task 4: Implement Shared URL Canonicalization and Platform Identity

**Files:**
- Create: `src/pkb/knowledge/urls.py`
- Test: `tests/test_knowledge_urls.py`

- [ ] **Step 1: Write failing canonicalization tests**

```python
from pkb.knowledge.urls import canonicalize_url, platform_identity


def test_canonicalize_url_removes_tracking_and_fragment():
    value = canonicalize_url("HTTPS://Example.COM:443/a/?utm_source=x&keep=1#part")
    assert value == "https://example.com/a?keep=1"


def test_platform_identity_matches_zhihu_api_and_public_urls():
    assert platform_identity("https://www.zhihu.com/api/v4/answers/123") == "zhihu:answer:123"
    assert platform_identity("https://www.zhihu.com/question/9/answer/123") == "zhihu:answer:123"
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_knowledge_urls.py -v`  
Expected: FAIL because `pkb.knowledge.urls` does not exist.

- [ ] **Step 3: Implement one shared canonicalizer**

Use `urllib.parse.urlsplit`, lowercase scheme/host, remove default ports, normalize the trailing slash, sort remaining query pairs, and drop this explicit allowlist of tracking keys:

```python
TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "source", "spm", "ref", "ref_src",
}
```

Add regexes for Zhihu API/public answer, article, and zvideo URLs. Return `None` for unknown platforms.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_knowledge_urls.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/knowledge/urls.py tests/test_knowledge_urls.py
git commit -m "feat: canonicalize URLs and parse platform identities"
```

### Task 5: Normalize Zhihu and X Records

**Files:**
- Create: `src/pkb/sources/zhihu.py`
- Create: `src/pkb/sources/x_bookmarks.py`
- Create: `tests/fixtures/knowledge/zhihu-10.jsonl`
- Create: `tests/fixtures/knowledge/x-bookmarks.jsonl`
- Test: `tests/test_source_adapters.py`

- [ ] **Step 1: Add minimal fixtures**

`zhihu-10.jsonl`:

```json
{"id":"zhihu_answer_123","source":"zhihu","title":"学习方法","author":"甲","content":"先理解，再练习。","url":"https://www.zhihu.com/api/v4/answers/123","created_at":"2025-01-01T00:00:00Z","images":[]}
```

`x-bookmarks.jsonl`:

```json
{"id":"tweet-1","url":"https://x.com/a/status/1","text":"值得保存","authorName":"乙","postedAt":"2025-01-02T00:00:00Z","links":["https://www.zhihu.com/question/9/answer/123?utm_source=x"]}
```

- [ ] **Step 2: Write failing adapter tests**

```python
import json
from pathlib import Path

from pkb.sources.x_bookmarks import XBookmarkAdapter
from pkb.sources.zhihu import ZhihuAdapter


def test_zhihu_adapter_uses_platform_identity():
    record = json.loads(Path("tests/fixtures/knowledge/zhihu-10.jsonl").read_text(encoding="utf-8"))
    doc = ZhihuAdapter().normalize(record, raw_path=Path("zhihu-10.jsonl"), raw_line=1)
    assert doc.identity_key == "zhihu:answer:123"
    assert doc.membership.collection_id == "10"


def test_x_adapter_prefers_bookmarked_target_identity():
    record = json.loads(Path("tests/fixtures/knowledge/x-bookmarks.jsonl").read_text(encoding="utf-8"))
    doc = XBookmarkAdapter().normalize(record, raw_path=Path("x-bookmarks.jsonl"), raw_line=1)
    assert doc.identity_key == "zhihu:answer:123"
    assert doc.membership.source == "x"
```

- [ ] **Step 3: Verify failure**

Run: `python -m pytest tests/test_source_adapters.py -v`  
Expected: FAIL because adapters do not exist.

- [ ] **Step 4: Implement adapters**

Both adapters must call `canonicalize_url` and `platform_identity`. `ZhihuAdapter` derives the collection ID from `zhihu-<id>.jsonl`; `XBookmarkAdapter` uses the first canonical HTTP link as target, falls back to the tweet URL, and never resolves redirects over the network.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_source_adapters.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/sources tests/fixtures/knowledge tests/test_source_adapters.py
git commit -m "feat: normalize Zhihu and X knowledge records"
```

### Task 6: Separate Source and Normalized Fingerprints

**Files:**
- Create: `src/pkb/knowledge/fingerprint.py`
- Test: `tests/test_fingerprint.py`

- [ ] **Step 1: Write failing fingerprint tests**

```python
from pkb.knowledge.fingerprint import normalized_content_hash, source_content_hash


def test_source_hash_ignores_acquisition_metadata():
    a = {"title": "T", "content": "C", "url": "u", "saved_at": "one"}
    b = {"title": "T", "content": "C", "url": "u", "saved_at": "two"}
    assert source_content_hash(a) == source_content_hash(b)


def test_normalized_hash_changes_with_normalized_body():
    assert normalized_content_hash("T", "A", "C", "u", ()) != normalized_content_hash("T", "A", "D", "u", ())
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_fingerprint.py -v`  
Expected: FAIL because fingerprint functions do not exist.

- [ ] **Step 3: Implement SHA-256 over stable JSON**

`source_content_hash` serializes only `id`, `title`, `author`/`authorName`, `content`/`text`, `url`, `links`, `images`, and source creation time with sorted keys and compact separators. `normalized_content_hash` serializes its explicit arguments. Both return lowercase hexadecimal SHA-256.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_fingerprint.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/knowledge/fingerprint.py tests/test_fingerprint.py
git commit -m "feat: separate source and normalized fingerprints"
```

### Task 7: Create Versioned SQLite Schema and Atomic Rebuild

**Files:**
- Create: `src/pkb/knowledge/migrations.py`
- Test: `tests/test_knowledge_migrations.py`

- [ ] **Step 1: Write failing schema test**

```python
import sqlite3

from pkb.knowledge.migrations import migrate


def test_migration_creates_core_tables(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"documents", "source_memberships", "document_url_aliases", "media", "jobs", "derivations", "reading_state"} <= tables
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_knowledge_migrations.py -v`  
Expected: FAIL because `migrate` does not exist.

- [ ] **Step 3: Implement migration 1**

Create tables and foreign keys matching Sections 9 and 10 of the approved design. Required uniqueness constraints:

```sql
UNIQUE(identity_key)
UNIQUE(source, source_item_id, collection_id)
UNIQUE(url)
UNIQUE(job_type, document_id, input_hash, pipeline_version)
```

Enable `PRAGMA foreign_keys=ON`, use a transaction, and set `PRAGMA user_version=1` only after all statements succeed.

- [ ] **Step 4: Add atomic rebuild helper test and implementation**

Test that `rebuild_database(target, builder)` leaves the existing target untouched when `builder` raises, and replaces it when `PRAGMA integrity_check` returns `ok`. Implement via a sibling `.tmp` file and `Path.replace`.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_knowledge_migrations.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/knowledge/migrations.py tests/test_knowledge_migrations.py
git commit -m "feat: add versioned knowledge database schema"
```

### Task 8: Implement Transactional Repository and Cross-Source Merge

**Files:**
- Create: `src/pkb/knowledge/repository.py`
- Test: `tests/test_knowledge_repository.py`

- [ ] **Step 1: Write failing upsert test**

```python
def test_upsert_keeps_one_document_with_two_memberships(repository, zhihu_doc, x_doc):
    first = repository.upsert_document(zhihu_doc, source_hash="s1", normalized_hash="n1", normalization_version=1)
    second = repository.upsert_document(x_doc, source_hash="s2", normalized_hash="n1", normalization_version=1)
    assert first.document_id == second.document_id
    assert repository.count_documents() == 1
    assert repository.count_memberships(first.document_id) == 2
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_knowledge_repository.py::test_upsert_keeps_one_document_with_two_memberships -v`  
Expected: FAIL because the repository does not exist.

- [ ] **Step 3: Implement transactional upsert**

Resolution order is `identity_key`, recognized platform identity from aliases, exact canonical URL alias, then exact source hash only for compatible identity-less types. Upsert document, alias, membership, and media in one transaction. Return:

```python
@dataclass(frozen=True)
class UpsertResult:
    document_id: int
    created: bool
    normalized_changed: bool
    source_changed: bool
```

- [ ] **Step 4: Add explicit merge conflict tests and implementation**

Test `merge_documents(survivor_id, duplicate_id, reason, reading_state_policy="reject")` moves memberships, aliases, non-conflicting reading state, and user tags transactionally, records a `document_merges` audit row, and refuses conflicting non-empty human notes.

Human reading state follows these rules:

- a missing/default state yields to an explicit state;
- identical explicit states merge without intervention and keep the newest `last_reviewed_at`;
- different explicit statuses, manual priorities, or reasons raise `MergeConflictError` under the default `reject` policy;
- an operator may rerun with `reading_state_policy="survivor"` or `"newest_reviewed"`; the chosen policy and both prior states are stored in the merge audit payload.

Add tests for `read` versus `unread`, priority 5 versus priority 2, and the two explicit resolution policies. No merge may silently discard a human state.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_knowledge_repository.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/knowledge/repository.py tests/test_knowledge_repository.py
git commit -m "feat: persist and merge normalized documents"
```

### Task 9: Benchmark and Select Chinese Lexical Search

**Files:**
- Create: `tests/fixtures/search_queries.json`
- Create: `tools/benchmark_search.py`
- Create: `docs/knowledge-search-benchmark.md`
- Modify: `pyproject.toml`
- Test: `tests/test_search_benchmark.py`

- [ ] **Step 1: Create an acceptance set**

The JSON file contains at least 30 queries sampled from real archived titles/bodies, including:

```json
[
  {"query":"学习方法","expected_ids":["zhihu:answer:123"]},
  {"query":"先理解","expected_ids":["zhihu:answer:123"]},
  {"query":"理解","expected_ids":["zhihu:answer:123"]},
  {"query":"练习","expected_ids":["zhihu:answer:123"]}
]
```

Expand `tests/fixtures/knowledge` with deterministic records until this committed acceptance file contains 30 queries: five one/two-character queries, five mixed-language queries, ten title matches, and ten body-only matches. Every `expected_ids` value must be an identity present in those committed fixtures. Create a separate ignored `data/state/search-queries-real.json` by manually labeling at least 30 results from the archived corpus; use it for the real-corpus run without committing personal titles or IDs.

- [ ] **Step 2: Write the failing benchmark contract test**

```python
from tools.benchmark_search import evaluate


def test_search_acceptance_requires_recall_and_latency():
    result = evaluate(expected={"q": {"a"}}, actual={"q": ["a", "b"]}, elapsed_ms=[2.0])
    assert result.recall_at_10 == 1.0
    assert result.p95_ms < 50
```

- [ ] **Step 3: Implement deterministic metrics**

`evaluate` calculates macro recall@10, mean reciprocal rank, p95 latency, index bytes, and unsupported-query count. The script creates temporary trigram and Jieba-pretokenized indexes over the same normalized corpus and emits JSON plus a Markdown table.

- [ ] **Step 4: Add optional search dependency**

```toml
[project.optional-dependencies]
search = ["jieba>=0.42.1"]
```

- [ ] **Step 5: Run the fixture and real-corpus benchmark**

Run:

```powershell
python -m pytest tests/test_search_benchmark.py -v
python tools/benchmark_search.py --raw-dir data/raw --queries tests/fixtures/search_queries.json --output data/state/search-benchmark.json
```

Acceptance: selected strategy has recall@10 >= 0.90, MRR >= 0.75, p95 < 100 ms on the current corpus, and zero unsupported acceptance queries. Record SQLite version, corpus hash, results, and the selected default in `docs/knowledge-search-benchmark.md`.

- [ ] **Step 6: Commit benchmark code and decision document**

```powershell
git add pyproject.toml tools/benchmark_search.py tests/fixtures/search_queries.json tests/test_search_benchmark.py docs/knowledge-search-benchmark.md
git commit -m "test: establish Chinese lexical search baseline"
```

### Task 10: Implement the Selected FTS Search Projection

**Files:**
- Create: `src/pkb/knowledge/search.py`
- Modify: `src/pkb/knowledge/migrations.py`
- Test: `tests/test_knowledge_search.py`

- [ ] **Step 1: Write failing search tests**

```python
def test_search_returns_ranked_source_context(search_index):
    results = search_index.search("学习方法", limit=10)
    assert results[0].title == "学习方法"
    assert results[0].memberships[0].source == "zhihu"
    assert "<mark>" in results[0].snippet


def test_search_filters_source_and_collection(search_index):
    results = search_index.search("学习", source="zhihu", collection_id="10", limit=10)
    assert all(item.memberships[0].collection_id == "10" for item in results)
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_knowledge_search.py -v`  
Expected: FAIL because the search projection does not exist.

- [ ] **Step 3: Implement the benchmark winner**

If trigram won, create the FTS table with:

```sql
CREATE VIRTUAL TABLE documents_fts USING fts5(
  title, content, summary, tags,
  content='documents_search_content', content_rowid='document_id',
  tokenize='trigram'
);
```

If Jieba won, store pre-tokenized text in dedicated indexed columns and preserve original text for snippets. Implement `SearchResult` as an immutable dataclass and use parameterized SQL for all filters.

- [ ] **Step 4: Run acceptance tests and commit**

Run: `python -m pytest tests/test_knowledge_search.py tests/test_search_benchmark.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/knowledge/search.py src/pkb/knowledge/migrations.py tests/test_knowledge_search.py
git commit -m "feat: add Chinese-capable full-text search"
```

### Task 11: Build the Incremental Index Coordinator and Reports

**Files:**
- Create: `src/pkb/knowledge/indexer.py`
- Create: `src/pkb/knowledge/reports.py`
- Test: `tests/test_knowledge_indexer.py`

- [ ] **Step 1: Write failing idempotency test**

```python
def test_second_index_run_is_unchanged(indexer, fixture_raw_dir):
    first = indexer.build(fixture_raw_dir)
    second = indexer.build(fixture_raw_dir)
    assert first.created > 0
    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged == first.processed
    assert second.derivation_jobs_queued == 0
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_knowledge_indexer.py -v`  
Expected: FAIL because the indexer does not exist.

- [ ] **Step 3: Implement scan, adapter routing, and reports**

Route `zhihu-*.jsonl` to `ZhihuAdapter` and `x-bookmarks*.jsonl` to `XBookmarkAdapter`; exclude `.sample` and backup paths by default. Catch malformed lines with path/line diagnostics. Return and serialize:

```python
@dataclass(frozen=True)
class IndexReport:
    discovered: int
    processed: int
    created: int
    updated: int
    unchanged: int
    skipped: int
    failed: int
    derivation_jobs_queued: int
```

Normalization-version changes update the normalized projection and report stale derivations but queue no AI jobs.

- [ ] **Step 4: Add strict-mode rollback test**

Add a malformed middle line. Assert default mode continues with `failed == 1`; strict mode raises and returns non-zero through the CLI without corrupting prior committed records.

- [ ] **Step 5: Add source-edit invalidation test**

```python
def test_source_content_edit_queues_new_derivation_and_preserves_old(indexer, repository, fixture_raw_dir):
    first = indexer.build(fixture_raw_dir)
    document_id = repository.find_by_identity("zhihu:answer:123").id
    old = repository.insert_test_derivation(document_id, source_hash=repository.get_document(document_id).source_content_hash)

    rewrite_fixture_body(fixture_raw_dir, identity="zhihu:answer:123", body="先理解，再刻意练习。")
    second = indexer.build(fixture_raw_dir)

    assert second.updated == 1
    assert second.derivation_jobs_queued == 1
    assert repository.get_derivation(old.id).id == old.id
    assert repository.pending_jobs(document_id)[0].input_hash == repository.get_document(document_id).source_content_hash
```

Define `rewrite_fixture_body` in this test file to parse the fixture JSONL, replace only the matching record's `content`, and rewrite the temporary fixture copy. The test must prove that a real source edit changes `source_content_hash`, queues exactly one new job, and retains the old derivation for audit. A second unchanged build after the edit queues zero additional jobs.

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest tests/test_knowledge_indexer.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/knowledge/indexer.py src/pkb/knowledge/reports.py tests/test_knowledge_indexer.py
git commit -m "feat: build incremental knowledge index"
```

### Task 12: Add Index and Search CLI Commands

**Files:**
- Modify: `src/pkb/cli.py`
- Test: `tests/test_knowledge_cli.py`

- [ ] **Step 1: Write failing CLI tests**

```python
import json

from pkb.cli import main


def test_index_build_and_json_search(tmp_path, fixture_raw_dir, capsys):
    db = tmp_path / "knowledge.db"
    assert main(["index", "build", "--raw-dir", str(fixture_raw_dir), "--db", str(db)]) == 0
    assert main(["search", "学习", "--db", str(db), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload[0]["title"] == "学习方法"
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_knowledge_cli.py -v`  
Expected: FAIL because commands are unsupported.

- [ ] **Step 3: Add thin dispatch**

Commands:

```text
pkb index build --raw-dir --db [--strict] [--report]
pkb index status --db --format text|json
pkb index rebuild --raw-dir --db [--report]
pkb search QUERY --db [--source] [--collection] [--limit] [--format text|json]
```

Move Phase 2 parser creation to `_add_knowledge_parsers(subparsers)` if `cli.py` would exceed 500 lines. CLI runners construct services and format results; they contain no SQL.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_knowledge_cli.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/cli.py tests/test_knowledge_cli.py
git commit -m "feat: expose index and search commands"
```

## Phase 1 Acceptance Gate

- [ ] Full suite passes offline.
- [ ] Real corpus indexes with every skipped/failed line explained.
- [ ] Second build queues zero work when inputs are unchanged.
- [ ] X bookmark target and native Zhihu item integration fixture becomes one document with two memberships.
- [ ] Chinese benchmark meets the recorded acceptance thresholds.
- [ ] Delete a copy of `knowledge.db`, rebuild it, and compare document/membership/media counts and a deterministic logical export hash.

---

# Phase 2 — Versioned AI Article Derivation

### Task 13: Define and Validate Derivation Schemas

**Files:**
- Create: `src/pkb/derive/models.py`
- Test: `tests/test_derivation_models.py`

- [ ] **Step 1: Write failing validation tests**

```python
import pytest

from pkb.derive.models import DerivationValidationError, validate_article_derivation


def test_derivation_requires_grounded_citations():
    payload = {
        "summary": "先理解再练习",
        "key_points": ["先理解"],
        "topics": [{"name": "学习", "confidence": 0.9}],
        "tags": [{"name": "方法", "confidence": 0.8}],
        "content_type": "tutorial",
        "evergreen_score": 4,
        "reading_priority": 3,
        "priority_reason": "可复用",
        "source_citations": [{"claim": "先理解", "excerpt": "先理解，再练习"}],
    }
    result = validate_article_derivation(payload, source_text="先理解，再练习。")
    assert result.summary == "先理解再练习"


def test_derivation_rejects_missing_excerpt():
    payload = {
        "summary": "先理解再练习",
        "key_points": ["先理解"],
        "topics": [{"name": "学习", "confidence": 0.9}],
        "tags": [{"name": "方法", "confidence": 0.8}],
        "content_type": "tutorial",
        "evergreen_score": 4,
        "reading_priority": 3,
        "priority_reason": "可复用",
        "source_citations": [{"claim": "先理解", "excerpt": "不在正文中的引文"}],
    }
    with pytest.raises(DerivationValidationError, match="excerpt"):
        validate_article_derivation(payload, source_text="无关正文")
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_derivation_models.py -v`  
Expected: FAIL because validation does not exist.

- [ ] **Step 3: Implement strict standard-library validation**

Use frozen dataclasses. Reject unknown top-level keys, invalid content type, scores outside 0–5, more than 8 tags, more than 5 topics, more than 10 key points, and excerpts not found after whitespace normalization in source text.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_derivation_models.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/derive/models.py tests/test_derivation_models.py
git commit -m "feat: validate grounded article derivations"
```

### Task 14: Add Versioned Prompts and Provider Contract

**Files:**
- Create: `src/pkb/derive/prompts.py`
- Create: `src/pkb/derive/provider.py`
- Modify: `.env.example`
- Test: `tests/test_derivation_provider.py`

- [ ] **Step 1: Write failing fake-provider test**

```python
from pkb.derive.provider import FakeDerivationProvider


def test_fake_provider_records_request_without_network():
    provider = FakeDerivationProvider([{"summary": "s"}])
    result = provider.complete(system="rules", user="content")
    assert result == {"summary": "s"}
    assert provider.requests == [("rules", "content")]
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_derivation_provider.py -v`  
Expected: FAIL.

- [ ] **Step 3: Implement provider protocol and HTTP client**

```python
class DerivationProvider(Protocol):
    provider_name: str
    model_name: str

    def complete(self, *, system: str, user: str) -> Mapping[str, object]: ...
```

Implement `OpenAICompatibleProvider` with `urllib.request`, JSON response format, explicit connect/read timeout, and exceptions `ProviderAuthError`, `ProviderRateLimitError`, `ProviderInvalidRequestError`, and `ProviderTemporaryError`. Never include API keys in exception text.

- [ ] **Step 4: Add versioned prompt**

Set `ARTICLE_PROMPT_VERSION = "article-v1"`. The system prompt requires JSON only, source-grounded excerpts, Chinese output unless the source is predominantly another language, and no external facts.

- [ ] **Step 5: Document environment names**

Add blank examples:

```dotenv
PKB_LLM_BASE_URL=
PKB_LLM_API_KEY=
PKB_LLM_MODEL=
PKB_LLM_TIMEOUT=60
```

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest tests/test_derivation_provider.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/derive/prompts.py src/pkb/derive/provider.py .env.example tests/test_derivation_provider.py
git commit -m "feat: add versioned AI provider contract"
```

### Task 15: Implement Leased Jobs With Heartbeats and Recovery

**Files:**
- Create: `src/pkb/derive/jobs.py`
- Modify: `src/pkb/knowledge/repository.py`
- Test: `tests/test_derivation_jobs.py`

- [ ] **Step 1: Write failing expired-lease test**

```python
from datetime import datetime, timedelta, timezone


def test_expired_running_job_is_reclaimed(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = job_queue.enqueue("article", 1, "hash", "v1")
    first = job_queue.claim("worker-a", now=now, lease=timedelta(minutes=15))
    second = job_queue.claim("worker-b", now=now + timedelta(minutes=16), lease=timedelta(minutes=15))
    assert first.id == second.id == job_id
    assert second.worker_id == "worker-b"
    assert second.attempts == 2
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_derivation_jobs.py -v`  
Expected: FAIL because the queue does not exist.

- [ ] **Step 3: Implement atomic lease operations**

Implement `enqueue`, `claim`, `heartbeat`, `succeed`, and `fail` with `BEGIN IMMEDIATE`. `claim` selects pending jobs or running jobs whose `lease_expires_at <= now`. Default lease is 15 minutes and max attempts is 3. The fourth claim moves the job to dead-letter and continues to the next eligible job.

- [ ] **Step 4: Add crash and ownership tests**

Assert a non-owner cannot heartbeat or complete a lease, an unexpired lease cannot be stolen, and an expired lease records `lease_expired` in job events.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_derivation_jobs.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/derive/jobs.py src/pkb/knowledge/repository.py tests/test_derivation_jobs.py
git commit -m "feat: add recoverable leased derivation jobs"
```

### Task 16: Build the Bounded Article Derivation Pipeline

**Files:**
- Create: `src/pkb/derive/pipeline.py`
- Test: `tests/test_derivation_pipeline.py`

- [ ] **Step 1: Write failing idempotency test**

```python
def test_pipeline_does_not_call_provider_twice_for_same_input(pipeline, fake_provider, indexed_document):
    first = pipeline.run(limit=1)
    second = pipeline.run(limit=1)
    assert first.succeeded == 1
    assert second.processed == 0
    assert len(fake_provider.requests) == 1
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_derivation_pipeline.py -v`  
Expected: FAIL because pipeline does not exist.

- [ ] **Step 3: Implement bounded processing**

For each claimed job: load normalized document, heartbeat before provider call, render the versioned prompt, call provider, validate, write a sanitized append-only run JSONL record, insert immutable derivation with `source_content_hash`, `normalized_content_hash`, and `normalization_version`, update FTS summary/tags, then succeed the job.

Stop the batch immediately on auth/quota/invalid-request errors. Retry temporary/rate-limit errors according to provider policy. Never promote invalid JSON.

- [ ] **Step 4: Add failure-contract tests**

Use fake providers for malformed output, timeout, 401, 429, and temporary 500. Assert states and exit summaries exactly match the approved error policy.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_derivation_pipeline.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/derive/pipeline.py tests/test_derivation_pipeline.py
git commit -m "feat: derive grounded article knowledge in bounded batches"
```

### Task 17: Add Derivation and Explicit Normalization-Migration CLI

**Files:**
- Modify: `src/pkb/cli.py`
- Test: `tests/test_derivation_cli.py`

- [ ] **Step 1: Write failing dry-run test**

```python
def test_normalization_migration_dry_run_queues_nothing(main_with_fake_provider, db, capsys):
    code = main_with_fake_provider([
        "derive", "migrate", "--db", str(db),
        "--normalization-version", "2", "--limit", "10", "--dry-run",
    ])
    assert code == 0
    assert "would_queue=" in capsys.readouterr().out
    assert count_pending_jobs(db) == 0
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_derivation_cli.py -v`  
Expected: FAIL because derive commands are unsupported.

- [ ] **Step 3: Add commands and safety checks**

```text
pkb derive articles --db --limit N [--dry-run] [--report]
pkb derive status --db --format text|json
pkb derive retry --db --status failed --limit N
pkb derive migrate --db --normalization-version N --limit N [--dry-run]
```

Reject `--limit 0`; accept unbounded scope only with `--unlimited`, and print document count, model, prompt version, and estimated input characters before confirmation-free execution.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_derivation_cli.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/cli.py tests/test_derivation_cli.py
git commit -m "feat: expose bounded derivation workflows"
```

## Phase 2 Acceptance Gate

- [ ] Fake-provider end-to-end tests pass offline.
- [ ] A stratified 20-document real sample is derived with explicit cost scope.
- [ ] Every accepted citation excerpt exists in its source text.
- [ ] Re-running unchanged inputs causes zero provider calls.
- [ ] Changing normalization version marks results stale but queues zero jobs until `derive migrate` is run.
- [ ] Kill a worker mid-job and confirm its expired lease is reclaimed.

---

# Phase 3 — Obsidian Projection

### Task 18: Render Deterministic Article and Index Markdown

**Files:**
- Create: `src/pkb/wiki/renderer.py`
- Test: `tests/test_wiki_renderer.py`

- [ ] **Step 1: Write failing renderer test**

```python
from pkb.wiki.renderer import render_article


def test_article_page_has_traceable_frontmatter(article_view):
    text = render_article(article_view)
    assert 'generated_by: "pkb"' in text
    assert 'source_content_hash: "' in text
    assert "## Summary" in text
    assert "See [[../user/" in text
    assert "先理解，再练习" in text
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_wiki_renderer.py -v`  
Expected: FAIL because renderer does not exist.

- [ ] **Step 3: Implement deterministic rendering**

Escape YAML strings with `json.dumps(..., ensure_ascii=False)`, sort tags/topics/collections by normalized name, use stable document IDs for filenames and wikilinks, and render empty derivation sections as `尚未生成` rather than inventing content.

- [ ] **Step 4: Add source/topic/tag index renderer tests**

Assert stable ordering and links for `render_source_index`, `render_topic_index`, and `render_tag_index`.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_wiki_renderer.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/wiki/renderer.py tests/test_wiki_renderer.py
git commit -m "feat: render deterministic knowledge Markdown"
```

### Task 19: Export Safely With a Generated-File Manifest

**Files:**
- Create: `src/pkb/wiki/exporter.py`
- Create: `vault/user/.gitkeep`
- Test: `tests/test_wiki_exporter.py`

- [ ] **Step 1: Write failing ownership test**

```python
def test_exporter_refuses_to_overwrite_human_file(exporter, vault):
    target = vault / "articles" / "doc-1.md"
    target.parent.mkdir(parents=True)
    target.write_text("human text", encoding="utf-8")
    result = exporter.export(vault)
    assert result.conflicts == 1
    assert target.read_text(encoding="utf-8") == "human text"
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_wiki_exporter.py -v`  
Expected: FAIL because exporter does not exist.

- [ ] **Step 3: Implement atomic generated-file ownership**

Generated pages include `generated_by: "pkb"`. Render to a temporary sibling and replace atomically. Write `vault/.pkb-generated.json` containing path, render fingerprint, renderer version, and document ID. Never write under `vault/user`.

- [ ] **Step 4: Implement check mode and intentional user-note links**

`check(vault)` reports broken generated wikilinks, missing source IDs, manifest/file mismatches, modified generated files, unmarked collisions, and stale generated files. Stale files are only removed with `remove_stale=True`.

Links from generated articles to `vault/user/<document-id>.md` are intentional Obsidian create-on-click links. The exporter never creates or modifies these human-owned files, and `check()` classifies a missing target under `creatable_user_notes` rather than `broken_links`. Add a test proving a missing user-note target does not fail `wiki check`, while a missing generated topic/source target does fail it.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_wiki_exporter.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/wiki/exporter.py vault/user/.gitkeep tests/test_wiki_exporter.py
git commit -m "feat: export safe Obsidian vault projection"
```

### Task 20: Add Wiki CLI and Image-Link Policy

**Files:**
- Modify: `src/pkb/cli.py`
- Test: `tests/test_wiki_cli.py`

- [ ] **Step 1: Write failing CLI test**

```python
def test_wiki_export_and_check(main_with_db, db, tmp_path):
    vault = tmp_path / "vault"
    assert main_with_db(["wiki", "export", "--db", str(db), "--vault", str(vault)]) == 0
    assert main_with_db(["wiki", "check", "--vault", str(vault)]) == 0
    assert list((vault / "articles").glob("*.md"))
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_wiki_cli.py -v`  
Expected: FAIL because commands are unsupported.

- [ ] **Step 3: Add commands**

```text
pkb wiki export --db --vault [--copy-attachments] [--report]
pkb wiki check --vault --format text|json
pkb wiki clean --vault --stale-only --dry-run
```

Default image behavior writes relative links to existing `data/images`; copying is opt-in and copies only media linked by exported articles.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_wiki_cli.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/cli.py tests/test_wiki_cli.py
git commit -m "feat: expose Obsidian export and integrity checks"
```

## Phase 3 Acceptance Gate

- [ ] Export a real Vault and open it in Obsidian.
- [ ] Inspect at least 20 pages across five collections.
- [ ] Confirm source URLs, images, Chinese filenames/links, and generated indexes work.
- [ ] Add a human note under `vault/user`, rerun export, and prove it is unchanged.
- [ ] Delete generated directories and reproduce the same manifest logical hash.

---

# Phase 4 — Daily Use Loop

### Task 21: Persist Human Reading State and Manual Tags

**Files:**
- Create: `src/pkb/review.py`
- Modify: `src/pkb/knowledge/repository.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write failing human-precedence test**

```python
def test_ai_refresh_cannot_replace_human_state(repository, document_id):
    repository.set_reading_state(document_id, status="read", priority=5, reason="manual")
    repository.set_user_tags(document_id, ["核心"])
    repository.apply_ai_tags(document_id, derivation_id=9, tags=[("核心", 0.2), ("学习", 0.9)])
    state = repository.get_reading_state(document_id)
    assert state.status == "read"
    assert state.priority == 5
    assert repository.get_user_tags(document_id) == ["核心"]
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_review.py::test_ai_refresh_cannot_replace_human_state -v`  
Expected: FAIL.

- [ ] **Step 3: Implement independent human tables and APIs**

Use separate reading-state and tag-origin rows. `set_reading_state` is an explicit human operation; AI functions only modify rows with `origin='ai'`.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_review.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/review.py src/pkb/knowledge/repository.py tests/test_review.py
git commit -m "feat: preserve human reading state and tags"
```

### Task 22: Generate a Deterministic Five-Item Daily Review

**Files:**
- Modify: `src/pkb/review.py`
- Modify: `src/pkb/wiki/renderer.py`
- Modify: `src/pkb/cli.py`
- Test: `tests/test_daily_review.py`

- [ ] **Step 1: Write failing selection test**

```python
def test_daily_review_is_bounded_and_diverse(review_service):
    items = review_service.select(date="2026-07-12", count=5)
    assert len(items) == 5
    assert len({item.primary_topic for item in items}) >= 3
    assert all(item.status in {"unread", "queued"} for item in items)
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_daily_review.py -v`  
Expected: FAIL.

- [ ] **Step 3: Implement transparent ranking**

Rank by manual priority first, then AI reading priority, evergreen score, never-reviewed age, and topic diversity. Seed tie-breaking with the review date for reproducibility. Exclude read, ignored, and items reviewed within 30 days.

- [ ] **Step 4: Add CLI and Markdown output**

```text
pkb review today --db --count 5 [--date YYYY-MM-DD] [--vault]
pkb review mark DOCUMENT_ID --db --status read|queued|ignored
```

When `--vault` is supplied, write `vault/daily/YYYY-MM-DD.md` atomically with article links and review prompts.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_daily_review.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/review.py src/pkb/wiki/renderer.py src/pkb/cli.py tests/test_daily_review.py
git commit -m "feat: add deterministic daily knowledge review"
```

## Phase 4 Acceptance Gate

- [ ] Use the daily queue for seven days or run a documented simulated seven-day fixture.
- [ ] Confirm read/ignored items do not reappear inside the exclusion window.
- [ ] Confirm manual priority overrides AI priority.
- [ ] Record usefulness notes before changing ranking weights.

---

# Phase 5 — Relationships, Retrieval Evaluation, and MCP

### Task 23: Generate Bounded Relation Candidates

**Files:**
- Create: `src/pkb/derive/relations.py`
- Test: `tests/test_relation_candidates.py`

- [ ] **Step 1: Write failing bound test**

```python
def test_relation_candidates_are_deduplicated_and_capped(candidate_builder, document_id):
    candidates = candidate_builder.for_document(document_id, per_document_limit=50)
    assert len(candidates) <= 50
    assert len({item.document_id for item in candidates}) == len(candidates)
    assert all(item.document_id != document_id for item in candidates)
    assert all(item.evidence_sources for item in candidates)
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_relation_candidates.py -v`  
Expected: FAIL.

- [ ] **Step 3: Implement bounded union**

Union candidates from shared confident topic/tag, top-K FTS title/summary/key-point neighbors, and same-collection saved-time proximity. Record which heuristics admitted each candidate, deduplicate, score deterministically, and cap before any provider call. Do not execute an all-pairs query.

- [ ] **Step 4: Add total-pair dry-run test**

Assert a corpus command with `--pair-limit 200` reports at most 200 pairs and makes zero provider calls in dry-run mode.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_relation_candidates.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/derive/relations.py tests/test_relation_candidates.py
git commit -m "feat: build bounded relation candidates"
```

### Task 24: Derive Evidence-Based Relations

**Files:**
- Modify: `src/pkb/derive/prompts.py`
- Modify: `src/pkb/derive/relations.py`
- Modify: `src/pkb/cli.py`
- Test: `tests/test_relation_pipeline.py`

- [ ] **Step 1: Write failing evidence test**

```python
def test_relation_without_source_evidence_is_rejected(relation_pipeline, fake_provider):
    fake_provider.responses = [{"type": "supports", "score": 0.9, "evidence": "not in either source"}]
    result = relation_pipeline.run(pair_limit=1)
    assert result.accepted == 0
    assert result.invalid == 1
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_relation_pipeline.py -v`  
Expected: FAIL.

- [ ] **Step 3: Implement relation validation and storage**

Allow `supports`, `contrasts`, `extends`, `example_of`, and `similar_to`. Require evidence excerpt to exist in at least one source and require an explanation referencing both document IDs. Store creating derivation ID and candidate heuristic evidence.

- [ ] **Step 4: Add bounded CLI**

```text
pkb derive relations --db --document-limit N --per-document-limit 50 --pair-limit N [--dry-run]
```

Reject unbounded relation runs without `--unlimited`.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_relation_pipeline.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add src/pkb/derive/prompts.py src/pkb/derive/relations.py src/pkb/cli.py tests/test_relation_pipeline.py
git commit -m "feat: derive grounded knowledge relationships"
```

### Task 25: Evaluate Semantic Retrieval Before Adding It

**Files:**
- Create: `src/pkb/semantic.py`
- Create: `tools/benchmark_semantic.py`
- Create: `docs/semantic-retrieval-decision.md`
- Test: `tests/test_semantic_benchmark.py`

- [ ] **Step 1: Extend the query set with semantic-only intents**

Add at least 20 paraphrase/concept queries to `tests/fixtures/search_queries.json`, each with human-reviewed relevant IDs. Keep lexical and semantic subsets labeled separately.

- [ ] **Step 2: Write failing comparison test**

```python
from pkb.semantic import materially_improves


def test_semantic_gate_requires_material_recall_gain():
    assert materially_improves(lexical_recall=0.70, hybrid_recall=0.82, minimum_gain=0.10)
    assert not materially_improves(lexical_recall=0.80, hybrid_recall=0.85, minimum_gain=0.10)
```

- [ ] **Step 3: Implement benchmark adapters, not production retrieval**

The tool accepts an OpenAI-compatible embedding endpoint or a deterministic fake fixture, caches vectors under ignored `data/index`, calculates recall@10/MRR/latency, and compares lexical versus hybrid reciprocal-rank fusion.

- [ ] **Step 4: Run the decision gate**

Acceptance for production semantic retrieval: hybrid recall@10 improves by at least 0.10 absolute on semantic queries, does not reduce lexical-query recall by more than 0.02, and p95 stays below 500 ms locally. Record costs, model, dimensions, corpus hash, and decision in `docs/semantic-retrieval-decision.md`.

- [ ] **Step 5: Commit benchmark and decision**

```powershell
git add src/pkb/semantic.py tools/benchmark_semantic.py tests/test_semantic_benchmark.py tests/fixtures/search_queries.json docs/semantic-retrieval-decision.md
git commit -m "test: evaluate semantic retrieval value"
```

### Task 26: Add Semantic Projection Only If Task 25 Passes

**Execution condition:** Execute this task only when `docs/semantic-retrieval-decision.md` records `decision: adopt`. If it records `decision: reject`, check this task as skipped with the report path and proceed to Task 27.

**Files:**
- Modify: `src/pkb/semantic.py`
- Modify: `src/pkb/knowledge/migrations.py`
- Modify: `src/pkb/knowledge/search.py`
- Modify: `src/pkb/cli.py`
- Test: `tests/test_semantic_search.py`

- [ ] **Step 1: Write failing invalidation test**

```python
def test_embeddings_recompute_only_for_changed_normalized_hash(semantic_index, documents):
    first = semantic_index.build(documents)
    second = semantic_index.build(documents)
    assert first.created == len(documents)
    assert second.created == 0
    assert second.unchanged == len(documents)
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_semantic_search.py -v`  
Expected: FAIL because production semantic projection is absent.

- [ ] **Step 3: Implement replaceable vector rows**

Store document ID, model, dimensions, `normalized_content_hash`, and packed float vector in SQLite. Compute cosine similarity in bounded batches for the current corpus and fuse lexical/semantic ranks using reciprocal-rank fusion. Do not make vectors the source of truth.

- [ ] **Step 4: Add CLI**

```text
pkb semantic build --db --limit N [--dry-run]
pkb search QUERY --db --mode lexical|hybrid
```

Default remains the mode selected in the decision report.

- [ ] **Step 5: Run acceptance and commit**

Run: `python -m pytest tests/test_semantic_search.py tests/test_semantic_benchmark.py -v && python -m pytest -q`  
Expected: PASS and benchmark thresholds remain satisfied.

```powershell
git add src/pkb/semantic.py src/pkb/knowledge/migrations.py src/pkb/knowledge/search.py src/pkb/cli.py tests/test_semantic_search.py
git commit -m "feat: add evidence-backed semantic retrieval"
```

### Task 27: Expose Stable Query Operations Through MCP

**Files:**
- Modify: `pyproject.toml`
- Create: `src/pkb/interfaces/mcp.py`
- Test: `tests/test_mcp_interface.py`

- [ ] **Step 1: Add optional dependency**

```toml
[project.optional-dependencies]
mcp = ["mcp>=1.0"]
```

Preserve existing `dev` and `search` groups.

- [ ] **Step 2: Write failing tool-contract test**

```python
def test_mcp_search_returns_cli_compatible_shape(mcp_service, query_service):
    expected = query_service.search("学习", limit=5).to_dicts()
    assert mcp_service.search_knowledge(query="学习", limit=5) == expected


def test_mcp_rejects_out_of_range_limit(mcp_service):
    with pytest.raises(ValueError, match="1..50"):
        mcp_service.search_knowledge(query="学习", limit=500)
```

- [ ] **Step 3: Implement localhost/stdio adapter**

Expose only:

- `search_knowledge(query, source?, collection?, limit=10)`;
- `read_document(document_id)`;
- `get_related(document_id, limit=10)`;
- `get_daily_review(date?, count=5)`;
- `set_reading_status(document_id, status)`.

The adapter calls existing services, contains no SQL, redacts raw cookies/config, defaults to stdio, and never exposes remote HTTP by default.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_mcp_interface.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add pyproject.toml src/pkb/interfaces/mcp.py tests/test_mcp_interface.py
git commit -m "feat: expose stable knowledge tools over MCP"
```

## Phase 5 Acceptance Gate

- [ ] Relation dry-run proves pair counts are bounded before model calls.
- [ ] Review at least 30 accepted relations and record precision.
- [ ] Semantic decision report contains a reproducible adopt/reject result.
- [ ] MCP contract results match CLI JSON result shapes.
- [ ] MCP runs locally without exposing secrets or binding a remote interface.

---

# Phase 6 — Optional Interface Expansion Decision

### Task 28: Measure Whether a Custom Web Interface Is Justified

**Files:**
- Create: `docs/web-interface-decision.md`
- Create: `tests/test_operations_documentation.py`

- [ ] **Step 1: Write the decision rubric before building UI**

Record at least four weeks of actual use or a user-approved shorter evaluation. Score:

- cross-device access need;
- inability to complete review workflow in Obsidian;
- need for visual relation review;
- need for nontechnical users;
- maintenance budget;
- security implications of remote access.

The report must end with exactly `decision: defer` or `decision: write-web-spec` and cite observed examples, not feature speculation.

- [ ] **Step 2: Add documentation-presence test**

```python
from pathlib import Path


def test_web_decision_has_explicit_gate():
    text = Path("docs/web-interface-decision.md").read_text(encoding="utf-8")
    assert "decision: defer" in text or "decision: write-web-spec" in text
```

- [ ] **Step 3: Run and commit the decision**

Run: `python -m pytest tests/test_operations_documentation.py -v`  
Expected: PASS.

```powershell
git add docs/web-interface-decision.md tests/test_operations_documentation.py
git commit -m "docs: decide whether a web interface is justified"
```

- [ ] **Step 4: Respect the architecture gate**

If the decision is `defer`, do not create frontend code. If it is `write-web-spec`, run a new brainstorming/design cycle for the web interface before creating its implementation plan. The approved architecture intentionally does not authorize an unspecified web application.

---

# Final Operations, Documentation, and Acceptance

### Task 29: Document Safe Operations and Recovery

**Files:**
- Modify: `README.md`
- Create: `docs/second-brain-operations.md`
- Test: `tests/test_operations_documentation.py`

- [ ] **Step 1: Extend documentation test**

```python
def test_operations_guide_covers_required_workflows():
    text = Path("docs/second-brain-operations.md").read_text(encoding="utf-8")
    for heading in (
        "## Build the Index", "## Search", "## Derive a Small Batch",
        "## Export the Vault", "## Daily Review", "## Recover Expired Jobs",
        "## Rebuild Projections", "## Back Up Human State",
    ):
        assert heading in text
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_operations_documentation.py -v`  
Expected: FAIL because the guide is incomplete.

- [ ] **Step 3: Write exact commands and recovery semantics**

Document finite first-run commands, dry-run output, which paths are evidence/projections/human state, provider privacy, backup requirements for `vault/user` and reading state, expired job recovery, normalization migration, index rebuild, and MCP startup.

- [ ] **Step 4: Update README roadmap and encoding**

Replace corrupted diagram characters, describe completed Phase 1, link the approved architecture and operations guide, and show the recommended daily workflow:

```text
index build -> search/derive small batch -> wiki export -> review today
```

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_operations_documentation.py -v && python -m pytest -q`  
Expected: PASS.

```powershell
git add README.md docs/second-brain-operations.md tests/test_operations_documentation.py
git commit -m "docs: add second brain operations and recovery guide"
```

### Task 30: Run Full Real-Data Acceptance Without Mutating Evidence

**Files:**
- Create: `tools/acceptance_second_brain.py`
- Create: `data/state/second-brain-acceptance.json` (generated, ignored)
- Test: `tests/test_acceptance_second_brain.py`

- [ ] **Step 1: Write failing fixture acceptance test**

```python
from tools.acceptance_second_brain import run_acceptance


def test_fixture_acceptance_is_reproducible(tmp_path, fixture_raw_dir):
    first = run_acceptance(fixture_raw_dir, tmp_path / "one")
    second = run_acceptance(fixture_raw_dir, tmp_path / "two")
    assert first.status == "ok"
    assert first.logical_hash == second.logical_hash
    assert first.raw_hash_before == first.raw_hash_after
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_acceptance_second_brain.py -v`  
Expected: FAIL because acceptance runner does not exist.

- [ ] **Step 3: Implement acceptance orchestration**

Hash every raw/frozen file before and after. Build a fresh index, run acceptance queries, use a fake provider for deterministic derivations, export/check a temporary Vault, run a second incremental pass, and compute a logical hash from sorted documents, memberships, derivations, and generated manifest entries. Fail if raw hashes change or the second pass creates unnecessary jobs.

- [ ] **Step 4: Run fixture and full suite**

Run:

```powershell
python -m pytest tests/test_acceptance_second_brain.py -v
python -m pytest -v
```

Expected: all tests pass offline.

- [ ] **Step 5: Run real-data acceptance**

```powershell
python tools/acceptance_second_brain.py --raw-dir data/raw --frozen-dir data/frozen --output data/state/second-brain-acceptance.json
```

Expected report:

- `status: ok`;
- zero raw/frozen hash changes;
- all failures/skips explained;
- second index pass has zero creates/updates;
- accepted Chinese search benchmark reference;
- reproducible projection logical hash.

- [ ] **Step 6: Commit acceptance code, not generated personal reports**

```powershell
git add tools/acceptance_second_brain.py tests/test_acceptance_second_brain.py
git commit -m "test: add reproducible second brain acceptance"
```

## Final Completion Gate

- [ ] All offline tests pass.
- [ ] Phase 1–5 acceptance gates pass; Task 26 is either passed or explicitly skipped by its decision report.
- [ ] Phase 6 has an explicit defer/spec decision and contains no unauthorized frontend implementation.
- [ ] Raw and frozen hashes are unchanged.
- [ ] SQLite and generated Vault can be rebuilt reproducibly.
- [ ] User notes, manual tags, and reading state survive all projection rebuilds.
- [ ] AI calls remain bounded, auditable, and source-grounded.
- [ ] README and operations documentation match the shipped CLI.

---

## Requirement Traceability

| Approved design requirement | Implementing tasks |
|---|---|
| Immutable Raw and rebuildable projections | 1, 7, 11, 19, 30 |
| Document/membership separation | 3, 5, 8 |
| Cross-source identity and URL aliases | 4, 5, 8 |
| Separate source/normalized hashes and explicit normalization migration | 6, 8, 11, 16, 17 |
| Chinese lexical retrieval is a Stage 1 gate | 9, 10, 12 |
| Versioned grounded AI derivations | 13, 14, 16, 17 |
| Expiring job leases and dead-letter recovery | 15, 16 |
| Obsidian as a safe deterministic view | 18, 19, 20 |
| Human state outranks AI | 19, 21, 22 |
| Bounded relation candidates | 23, 24 |
| Semantic retrieval only after evidence | 25, 26 |
| Stable CLI before MCP | 12, 17, 20, 22, 24, 27 |
| Web interface remains evidence-gated | 28 |
| Privacy, recovery, and real-data verification | 14, 15, 17, 29, 30 |
