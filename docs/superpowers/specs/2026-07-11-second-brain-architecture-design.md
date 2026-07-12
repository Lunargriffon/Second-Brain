# Second Brain Knowledge System Architecture Design

**Status:** Revised draft for approval  
**Date:** 2026-07-11  
**Project:** Personal Knowledge Base (`pkb`)  
**Scope:** Phase 2 knowledge indexing, AI derivation, Obsidian projection, and query interfaces

**Revision 2026-07-12:** Promoted Chinese lexical retrieval to a Stage 1 acceptance gate; separated source and normalized hashes; made normalization-driven AI regeneration explicit; specified cross-source identity matching, bounded relation candidates, and expiring job leases.

## 1. Executive Summary

The repository already provides a reliable acquisition and archive layer for personal knowledge sources. It currently contains a verified Zhihu archive, downloaded knowledge-bearing images, an X bookmark snapshot, resumable checkpoints, audit reports, frozen data snapshots, and an offline test suite.

The next phase will turn that archive into a usable knowledge system without replacing or mutating the existing raw data. The recommended architecture is a progressive hybrid:

1. Preserve raw source records and media as immutable evidence.
2. Build a reproducible SQLite knowledge index with FTS5 full-text search.
3. Store AI-generated summaries, tags, topics, and relationships as versioned derivations.
4. Export derived knowledge to an Obsidian-compatible Markdown vault.
5. Expose the same query layer through the CLI first and MCP later.
6. Defer a custom web application until usage demonstrates that Obsidian and CLI/MCP are insufficient.

This follows the useful parts of the LLM Wiki pattern: immutable raw sources, a maintained derived wiki, explicit schema and purpose, incremental ingestion, source traceability, and a clear separation between human and machine-authored knowledge.

## 2. Current State

The existing project includes:

- Source-specific Zhihu export, batch export, audit, verification, and checkpoint handling.
- A unified article JSON schema in `schema/article.v1.json`.
- Per-collection JSONL raw files and state files.
- Frozen snapshots and integrity manifests.
- Image extraction, triage, download, indexing, and quality reporting.
- A snapshot of X bookmarks.
- 63 passing offline tests at the time of this design.

The latest completeness report records 69 Zhihu collections, 1,139 archived raw rows, zero missing current unique items, and one archived item no longer returned by the live API. The image quality report records 3,520 downloaded knowledge images with no missing, zero-byte, or orphaned files on disk, apart from one candidate that was not included in the downloaded manifest.

The acquisition layer is therefore sufficiently mature to be treated as an upstream subsystem. Phase 2 should extend it rather than redesign it.

## 3. Goals

### 3.1 Primary goals

- Search all archived content by title, body, author, source, collection, tag, and topic.
- Avoid duplicated knowledge records when the same source item appears in multiple collections.
- Generate structured AI summaries and classifications incrementally.
- Preserve source traceability for every generated claim and relationship.
- Produce an Obsidian-compatible vault for immediate browsing and linking.
- Provide stable CLI services that can later be exposed through MCP or a web UI.
- Make every generated index and view reproducible from durable source files.
- Ensure user-authored notes, labels, and reading state are never overwritten by AI jobs.

### 3.2 Success criteria for the first release

- `pkb index build` creates or updates a SQLite index from current raw JSONL files.
- Re-running the indexer without source changes performs no unnecessary work.
- `pkb search <query>` returns ranked results with source title, URL, and collection context.
- Chinese lexical retrieval passes a recorded real-corpus acceptance set; plain `unicode61` tokenization is not shipped as the primary index.
- The same article saved into several collections appears as one document with several memberships.
- A bookmark targeting a known native source object resolves to that same document through shared URL and platform-ID rules.
- `pkb derive --limit N` creates validated, versioned derivation records for a bounded batch.
- A normalization-only code upgrade never silently queues corpus-wide AI regeneration.
- `pkb wiki export` produces an Obsidian vault whose generated pages link back to source records.
- A complete index and vault can be rebuilt without modifying `data/raw`, `data/frozen`, or original images.
- Interrupted index, derivation, and export commands can be safely rerun.
- The complete offline test suite passes without requiring a network connection or LLM credentials.

## 4. Non-goals

The first release will not include:

- A custom web frontend.
- A general-purpose autonomous research agent.
- A mandatory vector database.
- Automatic rewriting of raw records when the unified schema evolves.
- Automatic deletion of archived material because it disappeared from a source API.
- AI vision processing for every downloaded image.
- Automatic publication or sharing of personal knowledge.
- Multi-user accounts, permissions, billing, or cloud synchronization.
- An attempt to create a perfect global taxonomy before real usage data exists.

## 5. Architectural Principles

### 5.1 Raw data is evidence

Raw source records, downloaded media, and frozen snapshots are durable evidence. AI-generated content must never be written into raw JSONL records. Corrections to acquisition code affect future exports or explicitly versioned migrations, not silent rewrites of previously frozen data.

### 5.2 Derived data is replaceable and versioned

Search indexes, generated Markdown, embeddings, summaries, and relationships are projections. They may be deleted and rebuilt. AI derivations retain their model, prompt version, input fingerprint, generation time, and source references so results can be audited and compared.

### 5.3 Human state outranks AI state

User notes, manual tags, reading status, priority overrides, and corrections live in separate fields or files. Automated jobs may suggest changes but cannot overwrite explicit human decisions.

### 5.4 One stable knowledge core, multiple interfaces

CLI, Obsidian, MCP, and any future web application use the same query and repository services. Interface-specific behavior must not leak into source adapters or the domain model.

### 5.5 Start with lexical search

SQLite FTS5 is the first retrieval engine because the corpus is modest, Chinese keyword search is useful, it has no service dependency, and it is easy to rebuild. Semantic embeddings are an optional later projection, added only after retrieval evaluation shows a material gap.

### 5.6 Bounded automation

Network and model operations use explicit limits, retries, timeouts, checkpoints, and dry-run support. No initial command silently processes the entire corpus or incurs unbounded model cost.

## 6. System Context and Layering

```text
Zhihu collections    X bookmarks    Future PDF/RSS    Personal notes
        │                  │                │                │
        └──────────── source adapters and schema normalization ───────┐
                                                                       ▼
Layer 1: Immutable Raw
  data/raw/*.jsonl
  data/images/**
  data/frozen/**
  integrity manifests and acquisition reports
                                                                       │
                                  normalize, fingerprint, deduplicate  ▼
Layer 2: Knowledge Index
  data/index/knowledge.db
  documents, memberships, media, tags, relations, reading state, jobs
  FTS5 search index
                                                                       │
                               bounded, versioned AI derivation jobs   ▼
Layer 3: Derived Knowledge
  data/derived/articles/*.json
  data/derived/runs/*.jsonl
  summaries, key points, topics, tags, relationships, priority signals
                                                                       │
                                      stable query and export services ▼
Layer 4: Interfaces
  CLI                Obsidian vault             MCP              Future web
```

SQLite is a query projection and operational catalog, not the sole durable copy of knowledge. Raw and derived files remain sufficient to reconstruct it.

## 7. Proposed Repository Layout

```text
src/pkb/
├── cli.py                         # Command registration and dispatch
├── config.py                      # Existing environment/config helpers
├── sources/
│   ├── base.py                    # Source adapter protocol
│   ├── zhihu.py                   # Existing Zhihu client, moved gradually
│   └── x_bookmarks.py             # X bookmark adapter
├── ingest/
│   ├── exporter.py                # Existing acquisition workflow
│   ├── checkpoint.py              # Existing resumable state
│   ├── audit.py                   # Existing source audit
│   └── verify.py                  # Existing raw verification
├── knowledge/
│   ├── models.py                  # Domain value objects and typed contracts
│   ├── normalize.py               # Raw record -> NormalizedDocument
│   ├── fingerprint.py             # Identity and content fingerprints
│   ├── repository.py              # SQLite persistence boundary
│   ├── migrations.py              # Explicit database schema versions
│   └── indexer.py                 # Incremental index coordinator
├── derive/
│   ├── models.py                  # Structured derivation schemas
│   ├── prompts.py                 # Versioned prompt definitions
│   ├── provider.py                # OpenAI-compatible provider protocol
│   ├── pipeline.py                # Bounded derivation jobs
│   └── validation.py              # Output parsing and validation
├── query/
│   ├── search.py                  # Full-text search and filters
│   └── related.py                 # Evidence-based related documents
├── wiki/
│   ├── renderer.py                # Deterministic Markdown rendering
│   └── exporter.py                # Incremental vault projection
└── interfaces/
    └── mcp.py                     # Deferred until CLI contracts stabilize

data/
├── raw/                           # Existing immutable-ish acquisition files
├── images/                        # Existing downloaded media
├── frozen/                        # Existing accepted snapshots
├── index/
│   └── knowledge.db               # Rebuildable SQLite database
├── derived/
│   ├── articles/                  # Latest and/or versioned structured output
│   └── runs/                      # Append-only run manifests
└── state/                         # Job checkpoints, failures, and reports

vault/
├── _index/                        # Generated topic/tag/source indexes
├── articles/                      # Generated article pages; do not hand-edit
├── sources/                       # Generated collection/source pages
├── daily/                         # Generated review queues
├── user/                          # Human-authored notes; never overwritten
└── attachments/                   # Links or selected copied media
```

Existing modules should move only when Phase 2 changes touch them. A large unrelated migration is not a prerequisite.

## 8. Module Responsibilities

### 8.1 Source adapters

A source adapter maps source-specific raw records to a small neutral contract. It does not perform AI processing, write SQLite directly, or render Markdown.

Conceptual protocol:

```python
class SourceAdapter(Protocol):
    source_name: str

    def discover(self, location: Path) -> Iterable[RawRecordRef]: ...
    def normalize(self, record: Mapping[str, object]) -> NormalizedDocument: ...
```

### 8.2 Knowledge indexer

The indexer scans raw record references, asks the appropriate adapter to normalize them, computes identity and content fingerprints, and updates the repository in a transaction. It updates FTS rows and queues downstream work only for new or changed documents.

### 8.3 Repository

The repository owns SQLite access and transaction boundaries. Higher layers do not issue ad hoc SQL. It exposes operations such as:

- upsert document and source membership;
- replace media references for one source record;
- search documents with filters;
- record and claim jobs;
- store a derivation version;
- set human reading state;
- enumerate changed documents for wiki export.

### 8.4 Derivation pipeline

The derivation pipeline reads normalized documents, builds a versioned request, calls a provider, validates structured output, writes an immutable run record, and stores the accepted result. Provider transport and prompt definitions remain replaceable.

### 8.5 Wiki exporter

The exporter deterministically renders accepted normalized and derived data. It owns generated directories but never writes inside `vault/user`. Generated files contain a marker and source identity. Files without the marker are treated as human-owned and are not replaced.

### 8.6 Query service

The query service provides stable domain-level results to CLI and later MCP/Web adapters. Search results include document identity, highlighted snippets, score, source memberships, canonical URL, and derivation availability.

## 9. Core Data Model

### 9.1 `documents`

Represents one deduplicated knowledge item.

Key fields:

- `id`: stable internal identifier.
- `canonical_url`: normalized external URL when available.
- `title`, `author`, `plain_content`.
- `source_created_at`, `first_saved_at`, `last_seen_at`.
- `identity_key`: source-independent or source-qualified stable identity.
- `source_content_hash`: hash of a stable serialization of source knowledge fields before the versioned normalization pipeline. Volatile acquisition metadata such as `saved_at`, raw file location, and audit timestamps is excluded.
- `normalized_content_hash`: hash of the current normalized title, author, body, canonical URL, and ordered media references.
- `normalization_version`: explicit version of the normalization rules that produced the current normalized fields.
- `schema_version`.

### 9.2 `source_memberships`

Represents one document's occurrence in one source collection or feed.

Key fields:

- `document_id`, `source`, `source_item_id`.
- `collection_id`, optional collection title.
- `raw_path`, `raw_line` or another durable raw locator.
- `source_url`, `observed_at`.

Uniqueness is based on source, source item, and collection context. A single document may have several memberships.

### 9.3 `media`

- `document_id`, original remote URL, local path.
- media type, byte size, checksum.
- image triage bucket and triage reasons.
- source locator and image order.

### 9.4 `derivations`

Stores immutable accepted AI results.

- `id`, `document_id`, `kind`.
- `payload_json` validated against a derivation schema.
- `input_hash`, `prompt_version`, `provider`, `model`.
- generation parameters and timestamp.
- status and optional superseding derivation ID.

The current result is selected by policy; older versions remain available for auditing.

### 9.5 `tags`, `document_tags`, and `topics`

Tags record normalized and display forms. Assignments record origin (`ai` or `user`), confidence, and derivation ID where applicable. User assignments are never deleted by an AI refresh.

The first release should use a shallow topic hierarchy. It should not attempt to infer a universal ontology.

### 9.6 `relations`

- source and target document IDs.
- relation type such as `supports`, `contrasts`, `extends`, `example_of`, or `similar_to`.
- score, evidence excerpt or shared metadata.
- creating derivation ID.

No formal relation is stored without inspectable evidence. Low-confidence similarity may be returned dynamically without becoming a durable graph edge.

Relation generation never performs an all-pairs corpus comparison. For each source document, candidates are the bounded union of:

- documents sharing at least one sufficiently confident topic or normalized tag;
- the top `K` FTS results for title, summary, and key-point queries;
- documents in the same source collection within a configurable saved-time window;
- optional semantic neighbors if an embedding projection is introduced later.

The union is deduplicated and capped at 50 candidates per document by default before any LLM comparison. Commands expose both the per-document cap and a total pair limit, and their dry-run output reports the candidate-pair count and estimated cost.

### 9.7 `reading_state`

Human-controlled operational state:

- unread, queued, reading, read, archived, or ignored.
- manual priority and optional reason.
- last reviewed time.
- user note reference.

### 9.8 `jobs`

- job type and document identity.
- input hash and requested pipeline version.
- pending, running, succeeded, failed, or dead-letter status.
- attempts, timestamps, and sanitized error.
- `worker_id`, `leased_at`, `lease_expires_at`, and `heartbeat_at` for running jobs.

The uniqueness key prevents duplicate pending work for the same document, input, and pipeline version.

Claiming a job creates a time-bounded lease rather than a permanent `running` lock. A worker extends its lease with a heartbeat while processing. A pending claim operation may atomically reclaim an expired lease, increment the attempt count, and record `lease_expired` as the previous outcome. The initial default lease is 15 minutes and is configurable by job type. A job exceeding the normal retry limit moves to dead-letter instead of being reclaimed forever.

### 9.9 FTS5 index

The FTS projection indexes title, content, accepted summary, and normalized tags. Plain `unicode61` tokenization is not an acceptable default for this predominantly Chinese corpus because it does not provide useful CJK word boundaries.

Stage 1 therefore uses the FTS5 `trigram` tokenizer as the default Chinese-capable baseline. Before the database schema is accepted, a real-corpus benchmark must compare at least:

1. FTS5 `trigram` indexing; and
2. a lightweight pre-tokenized word index, such as Jieba output stored in a separate search column.

The benchmark covers common Chinese words and phrases, one- and two-character queries, mixed Chinese/English terms, punctuation, title-only matches, body matches, false positives, index size, and query latency. Trigram remains the default if it meets the recorded acceptance set. If the runtime SQLite build lacks trigram support or the benchmark fails, Stage 1 adopts pre-tokenization before search is considered complete. Semantic retrieval is not a substitute for fixing the lexical baseline.

## 10. Identity, Normalization, and Deduplication

Deduplication must not rely on title equality.

Identity precedence within a source:

1. Stable source object ID, such as a Zhihu answer/article ID.
2. Canonicalized URL for sources with stable canonical URLs.
3. Exact source content hash where a source ID is absent.
4. Near-duplicate detection only as an advisory result requiring review.

Cross-source matching is deterministic and uses an explicit alias layer:

1. Every adapter emits both the record URL and, for bookmarks, the bookmarked target URL when available.
2. Platform object IDs are parsed from recognized URLs. A bookmark target resolving to a known Zhihu answer or article ID maps to the same identity as the native Zhihu record.
3. URLs are canonicalized by a shared function, never by source-specific ad hoc rules. Host aliases, fragments, default ports, trailing-slash policy, and an allowlist of tracking parameters are normalized consistently.
4. Exact canonical target URL equality links records to one document and stores every observed URL in a `document_url_aliases` table.
5. Exact `source_content_hash` equality may merge records only when both lack a stronger stable identity and compatible content types are confirmed.
6. Redirect shorteners that cannot be resolved from stored expanded-link metadata remain unresolved during offline indexing. Network redirect resolution is a separate bounded reconciliation command; it never occurs implicitly during a rebuild.
7. Near matches are written to a review report and never silently merged.

When later evidence shows that two existing documents are identical, an explicit merge operation preserves both IDs as aliases, moves memberships and user state transactionally, and records an auditable merge event.

Normalization includes:

- Unicode normalization.
- Deterministic whitespace cleanup.
- Removal of non-content HTML while retaining readable text.
- URL canonicalization with known tracking parameters removed.
- Stable ordering and normalization of media URLs.
- Preservation of the original raw locator.
- Use of an explicit `normalization_version` for every normalized projection.

A document may update in place when its stable identity remains the same and its source knowledge fields change. The old raw evidence remains available, and a changed `source_content_hash` invalidates dependent AI derivations.

Changing normalization code has different semantics. It updates `normalization_version` and may change `normalized_content_hash`, FTS rows, or Wiki output, but it does not automatically enqueue corpus-wide AI work. The index report records how many existing derivations were produced under an older normalization version. Reusing them, selectively regenerating them, or invalidating them all is an explicit migration command with dry-run counts and normal batch limits.

## 11. Incremental Processing

### 11.1 Index build

```text
discover raw files
  -> read record with raw locator
  -> normalize through source adapter
  -> calculate identity key, source content hash, and normalized content hash
  -> transactionally upsert document, memberships, and media
  -> update FTS only if indexed fields changed
  -> enqueue derivation and wiki work only if relevant inputs changed
```

Unchanged input produces no new AI jobs.

### 11.2 Derivation invalidation

A derivation is automatically reusable only when all of the following match:

- document `source_content_hash`;
- derivation kind and schema version;
- prompt version;
- relevant provider policy version.

Each derivation also records the `normalization_version` and `normalized_content_hash` it observed for audit purposes. A normalization-only change marks the derivation as `normalization_stale` but does not enqueue a replacement. Regeneration requires an explicit migration selection such as `--normalization-version`, `--document-id`, or a bounded `--limit`; an unlimited migration requires the existing explicit unlimited flag.

Changing a model name alone likewise does not force corpus-wide regeneration. Model upgrades and normalization migrations are explicit policy decisions, never side effects of an ordinary index build.

### 11.3 Wiki invalidation

A page render fingerprint includes normalized content, selected derivation IDs, user-safe metadata, and renderer version. The exporter rewrites a generated page only when this fingerprint changes.

## 12. AI Derivation Contract

The first structured article derivation contains:

```json
{
  "summary": "concise factual summary",
  "key_points": ["..."],
  "topics": [{"name": "...", "confidence": 0.0}],
  "tags": [{"name": "...", "confidence": 0.0}],
  "content_type": "tutorial|argument|reference|story|news|other",
  "evergreen_score": 0,
  "reading_priority": 0,
  "priority_reason": "...",
  "source_citations": [{"claim": "...", "excerpt": "..."}]
}
```

Scores use documented bounded scales. The validator rejects unknown fields where practical, invalid types, excessively long outputs, citations that cannot be found in normalized source text, and tag counts above configured limits.

The initial derivation pipeline does not generate cross-document relations in the same call. Relation generation is a separate bounded step after summaries and tags are stable.

## 13. Obsidian Vault Design

Each generated article page uses YAML frontmatter and predictable sections:

```markdown
---
id: zhihu_answer_123
source: zhihu
source_url: https://...
collections:
  - collection-id
source_content_hash: "<sha256>"
normalized_content_hash: "<sha256>"
normalization_version: 1
derivation_id: ...
generated_by: pkb
---

# Article title

## Summary

## Key points

## Topics and tags

## Related knowledge

## Source

## Personal notes
See [[../user/zhihu_answer_123]]
```

Generated article files do not contain editable user prose. Personal notes are separate pages under `vault/user`, linked by stable ID. This prevents a regenerated page from destroying user content.

Topic, tag, source, and collection index pages are generated from SQLite. Daily review pages contain links and prompts but do not duplicate entire article bodies.

Attachment strategy for the first release is to link to existing files under `data/images` using relative or configured paths. Copying thousands of images into the vault is optional and disabled by default.

## 14. CLI Contract

### 14.1 Build and inspect the index

```powershell
pkb index build --raw-dir data/raw --db data/index/knowledge.db
pkb index status --db data/index/knowledge.db
pkb index rebuild --db data/index/knowledge.db
```

`build` is incremental. `rebuild` creates a new temporary database, validates it, and atomically replaces the old projection; it does not mutate raw data.

### 14.2 Search

```powershell
pkb search "学习方法"
pkb search "RAG" --source x --limit 20
pkb search "金融" --collection 716393975 --format json
```

Default output is concise terminal text. JSON output is stable enough for MCP and scripts.

### 14.3 Derive

```powershell
pkb derive articles --limit 10 --dry-run
pkb derive articles --limit 10 --provider openai-compatible
pkb derive retry --status failed --limit 5
pkb derive status
pkb derive migrate --normalization-version 2 --limit 10 --dry-run
```

The command prints the intended document count and configured model before making calls. An unlimited run requires an explicit flag.
`migrate` is the only path that regenerates derivations solely because normalization policy changed; ordinary index builds only report stale versions.

### 14.4 Wiki

```powershell
pkb wiki export --vault vault
pkb wiki check --vault vault
```

`check` detects broken generated links, missing source identities, accidental modifications in generated files, and collisions with human files.

### 14.5 Daily review, deferred within Phase 2

```powershell
pkb review today --count 5
pkb review mark <document-id> --status read
```

Review selection initially uses deterministic filters and priority scores, not a complex recommender.

## 15. Configuration and Secrets

Non-secret defaults live in a project configuration file or CLI defaults. Secrets remain in environment variables or `.env`, which must not be committed.

Suggested settings:

- database and vault paths;
- provider base URL, model, timeout, and concurrency;
- maximum documents and estimated token budget per run;
- prompt and derivation schema versions;
- output language;
- enabled source adapters.

Run manifests must redact authorization headers, cookies, API keys, and sensitive provider responses.

## 16. Error Handling and Recovery

### 16.1 Raw/index errors

- A malformed JSONL line records its path, line number, and sanitized error.
- Strict mode stops immediately; default batch mode continues and returns a non-zero summary if any records failed.
- Each raw record update is transactional.
- Database migrations run inside transactions and record a schema version.
- Rebuild writes to a temporary database and swaps only after integrity checks pass.

### 16.2 AI errors

- Provider calls have timeouts and bounded exponential backoff.
- Authentication, quota, and invalid-request errors stop the batch instead of retrying indefinitely.
- Rate limits pause or stop according to provider policy.
- Invalid structured output is retained in a sanitized run artifact for debugging but is not promoted to an accepted derivation.
- Failed jobs are retryable; repeated failures enter a dead-letter state requiring an explicit retry.
- Workers heartbeat while holding a job lease. Expired `running` leases are atomically reclaimable, counted as failed attempts, and reported separately from provider failures.

### 16.3 Wiki errors

- Pages are rendered to temporary files and replaced atomically.
- The exporter refuses to overwrite unmarked files.
- A generated-file manifest records paths and fingerprints.
- Stale generated files are reported before removal; automatic removal requires an explicit option.

### 16.4 Exit behavior

Commands return non-zero for incomplete requested operations, invalid configuration, integrity failures, or failed strict-mode records. Partial progress remains resumable and is summarized clearly.

## 17. Testing Strategy

### 17.1 Unit tests

- Source normalization, shared URL canonicalization, and platform object-ID extraction.
- Stable identity, source-content fingerprint, normalized fingerprint, and normalization-version behavior.
- Exact and cross-collection deduplication.
- Cross-source matching from an X bookmark target URL to the corresponding native Zhihu object.
- Derivation output validation and citation verification.
- Markdown rendering and generated-file ownership rules.
- Search query parsing and result mapping.

### 17.2 Repository integration tests

- SQLite migration from an empty database.
- Incremental indexing of new, unchanged, updated, and removed memberships.
- Transaction rollback on malformed records.
- FTS ranking and the accepted real-corpus Chinese/mixed-language retrieval benchmark, including short queries.
- Job claiming, heartbeat, expired-lease reclamation, retry, and dead-letter transitions.
- Rebuild-and-swap behavior.

### 17.3 Pipeline contract tests

- Fake AI provider returns valid, malformed, timed-out, rate-limited, and authentication-error responses.
- The pipeline never accepts invalid derivations.
- Identical input and pipeline versions do not trigger a second request.
- Prompt version changes create a new derivation without deleting the old one.

### 17.4 End-to-end offline test

A small fixture corpus containing duplicate memberships, images, Chinese text, X content, and a changed source record runs through:

```text
raw fixture -> index build -> search -> fake derivation -> wiki export -> wiki check
```

The test asserts deterministic outputs, valid links, preserved user notes, and successful rerun with zero unnecessary jobs.

### 17.5 Real-data acceptance

Before declaring the first release complete:

- Index all accepted raw files and reconcile counts against current reports.
- Explain every skipped or failed record.
- Validate representative Chinese and English searches manually.
- Derive a small stratified sample from several collections.
- Review generated summaries for factual grounding and citation coverage.
- Open the exported vault in Obsidian and inspect links, images, and indexes.
- Delete the test index and vault projection and prove they rebuild consistently.

## 18. Observability and Reports

Every major command prints and optionally writes a machine-readable report containing:

- discovered, processed, unchanged, created, updated, skipped, and failed counts;
- start/end time and duration;
- input paths and output projection versions;
- model and prompt version for AI work;
- sanitized error groups;
- whether more pending work remains.

Reports are timestamped under `data/state` and may have a small `latest.path` pointer, following existing repository conventions.

## 19. Security and Privacy

- The default system remains local-first.
- Raw personal content is sent to an external model only after explicit provider configuration and an explicit derive command.
- Dry-run displays scope before network use.
- Model requests include only the fields needed for the selected derivation.
- Secrets, cookies, and authorization data are not stored in derivation files or logs.
- A local OpenAI-compatible endpoint can be used without changing the pipeline contract.
- Future web or MCP interfaces bind to localhost by default and require an explicit decision before remote exposure.

## 20. Performance Expectations

The initial corpus is small enough for SQLite and single-process coordination. The design avoids introducing Redis, a task broker, Elasticsearch, or a dedicated vector database.

Expected behavior:

- Full incremental index scans finish locally in seconds to low minutes depending on content parsing.
- Search latency is interactive on the current corpus.
- AI processing is deliberately rate-limited and dominates runtime.
- Wiki export updates only changed pages.

Concurrency is initially one worker for derivations. A small configurable worker count may be added only if provider limits and deterministic job claiming are tested.

## 21. Evolution Path

### Stage 1: Searchable knowledge core

- Introduce source adapter and normalized document contracts.
- Add separate source and normalized hashes, explicit normalization versions, URL aliases, and deterministic cross-source matching.
- Add SQLite schema, migrations, repository, incremental indexer, and FTS search.
- Benchmark trigram and lightweight Chinese pre-tokenization on real saved queries; select and document a Chinese-capable lexical baseline before Stage 1 acceptance.
- Add index/search CLI commands and reconciliation reports.

### Stage 2: Versioned article derivation

- Add provider protocol, prompt/schema versioning, job state, structured validation, and bounded article derivation.
- Generate summary, key points, shallow topics/tags, and priority hints.

### Stage 3: Obsidian projection

- Add deterministic article pages, source/topic/tag indexes, generated manifest, and separate user notes.
- Add vault integrity checks.

### Stage 4: Daily use loop

- Add reading state and a five-item daily review queue.
- Gather actual searches, corrections, and manual tags to evaluate usefulness.

### Stage 5: Retrieval and integration upgrades

- Evaluate semantic retrieval against a small query benchmark.
- Add embeddings only if they materially improve recall.
- Stabilize JSON CLI contracts, then expose search/read/update-reading-state through MCP.

### Stage 6: Optional interface expansion

- Build a web interface only if cross-device access, visual review workflows, or graph exploration cannot be served adequately by Obsidian and MCP.

## 22. Major Design Decisions and Rationale

### SQLite rather than a hosted search service

The corpus size does not justify an external service. SQLite reduces operational burden, is easy to back up, supports FTS5, and fits the existing standard-library-oriented Python project.

### Markdown plus structured JSON rather than Markdown alone

Markdown is convenient for reading but awkward as the only machine state. Structured derivation JSON is the durable AI output; Markdown is a deterministic human-facing projection.

### Obsidian as a view rather than the database

This provides a polished interface immediately without making Obsidian-specific filesystem conventions the system's core storage model.

### No mandatory vector search in the first release

Lexical retrieval is transparent and testable. Embeddings add model dependencies, invalidation complexity, and opaque ranking. They should solve a measured retrieval problem, not be installed by default.

### No immediate adoption of LLM Wiki, Khoj, or RAGFlow as the data core

Those projects are valuable references and possible interfaces, but adopting their storage model would subordinate the already verified acquisition archive to a larger external system. The proposed core remains small and tailored while retaining the ability to export data to those tools for experiments.

## 23. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Default SQLite tokenization makes Chinese search ineffective | Treat Chinese lexical retrieval as a Stage 1 acceptance gate; benchmark trigram against lightweight pre-tokenization and ship one of them as the default. |
| AI summaries introduce unsupported claims | Require source excerpts, validate citations, keep raw content one click away, and support human correction. |
| Taxonomy becomes inconsistent | Start shallow, normalize names, cap tag counts, and preserve manual overrides. |
| Generated Markdown overwrites user edits | Separate generated and user directories; require generated markers and manifests. |
| Provider cost grows unexpectedly | Default limits, dry-run, token estimates, one worker, and explicit unlimited flag. |
| Same article is duplicated across sources or collections | Separate identity from memberships, share canonicalization rules, parse platform IDs from target URLs, store URL aliases, and test X-to-Zhihu matching. |
| Normalization changes trigger accidental corpus-wide AI calls | Separate source and normalized hashes; mark old derivations stale and require an explicit bounded migration to regenerate. |
| Relation generation becomes quadratic | Construct a bounded candidate union from shared metadata, FTS neighbors, and time proximity; cap pairs before model calls. |
| Crashed workers leave jobs permanently running | Use expiring leases and heartbeats, atomically reclaim expired jobs, and apply retry/dead-letter limits. |
| A schema change corrupts the only index | Treat SQLite as rebuildable; transactional migrations and rebuild-to-temp. |
| Refactoring acquisition breaks stable behavior | Move existing modules gradually and retain current tests throughout. |

## 24. Review Questions

External review should focus on:

1. Are the truth boundaries between Raw, SQLite, Derived JSON, and Markdown unambiguous?
2. Can all replaceable projections be rebuilt without losing user-authored state?
3. Is document identity sufficiently separated from collection membership?
4. Are AI invalidation and versioning rules deterministic?
5. Does the first release avoid unnecessary infrastructure while leaving clean extension points?
6. Are CLI contracts bounded and safe for personal data and model cost?
7. Are testing and acceptance criteria strong enough to detect Chinese retrieval and grounding failures?
8. Is the scope small enough to implement in staged plans without coupling acquisition, AI, and interfaces?

## 25. Approval Gate

This document defines architecture only. No implementation should begin until review comments are incorporated and the user approves the revised specification. After approval, the next artifact should be a task-by-task implementation plan beginning with Stage 1, not a single plan for all six stages.
