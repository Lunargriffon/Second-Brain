# Douyin Knowledge-Value Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover all Douyin favorites but retain, transcribe, index, and publish only videos whose own metadata indicates reusable knowledge value.

**Architecture:** Add a deterministic versioned metadata classifier and persist its auditable decision beside each manifest item. Classify before media acquisition, atomically filter the existing raw corpus with a backup, process only kept items, and publish index/Wiki outputs only when all kept items finish.

**Tech Stack:** Python 3.12, dataclasses, JSON/JSONL, pytest, Ruff, existing OpenCLI/yt-dlp/FunASR pipeline.

---

### Task 1: Add the versioned metadata classifier

**Files:**
- Create: `src/pkb/douyin/eligibility.py`
- Create: `tests/test_douyin_eligibility.py`

- [ ] **Step 1: Write failing classifier contract tests**

Test exact keep/exclude decisions for tutorials, analysis, beauty display,
scenery, travel guides, gaming highlights, conflicting signals, empty metadata,
Unicode normalization, stable input hashes, and absence of folder-name input.

```python
def test_tutorial_metadata_is_kept():
    decision = classifier().classify(metadata(caption="Python 自动化教程"))
    assert decision.eligibility is Eligibility.KEEP
    assert "knowledge_tutorial" in decision.reasons

def test_appreciation_and_travel_metadata_are_excluded():
    for caption in ("氛围感美女写真", "治愈风景壁纸", "三亚旅游攻略"):
        assert classifier().classify(metadata(caption=caption)).eligibility is Eligibility.EXCLUDE

def test_ambiguous_metadata_fails_closed():
    assert classifier().classify(metadata(caption="今天也要开心")).eligibility is Eligibility.EXCLUDE
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_douyin_eligibility.py -q`
Expected: FAIL because `pkb.douyin.eligibility` does not exist.

- [ ] **Step 3: Implement the minimal classifier**

Define `Eligibility`, `VideoMetadata`, `EligibilityDecision`,
`KnowledgeValueClassifier`, and `RuleBasedKnowledgeValueClassifier`. Normalize
with NFKC plus casefold, compute canonical SHA-256 over caption/hashtags/author,
and use explicit versioned positive/negative phrase groups. Strong exclusions
win except when at least two specific knowledge groups match. Empty and
ambiguous inputs return `exclude` with stable reason codes.

- [ ] **Step 4: Verify GREEN and lint**

Run: `python -m pytest tests/test_douyin_eligibility.py -q && ruff check src/pkb/douyin/eligibility.py tests/test_douyin_eligibility.py`
Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/douyin/eligibility.py tests/test_douyin_eligibility.py
git commit -m "feat: classify Douyin favorites by knowledge value"
```

### Task 2: Persist classification state and migrate manifests

**Files:**
- Modify: `src/pkb/douyin/models.py`
- Modify: `src/pkb/douyin/manifest.py`
- Modify: `tests/test_douyin_models.py`
- Modify: `tests/test_douyin_manifest.py`

- [ ] **Step 1: Write failing migration and invalidation tests**

Cover optional decision fields on `FavoriteItem`, immutable decision updates,
version-1 reads, version-2 writes, and reclassification when policy version or
input hash changes. Assert `pending()` returns only kept unfinished items and
never excluded items.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_douyin_models.py tests/test_douyin_manifest.py -q`
Expected: FAIL because classification fields and manifest operations are absent.

- [ ] **Step 3: Implement manifest schema version 2**

Add nullable `eligibility`, tuple `eligibility_reasons`, nullable
`classifier_version`, and nullable `classification_input_hash` with strict
serialization. Read versions 1 and 2; save version 2 atomically. Add
`set_eligibility()` and `needs_classification()`. Preserve media stage and all
source metadata when decisions change.

- [ ] **Step 4: Verify GREEN and adjacent compatibility**

Run: `python -m pytest tests/test_douyin_models.py tests/test_douyin_manifest.py tests/test_douyin_collector.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/douyin/models.py src/pkb/douyin/manifest.py tests/test_douyin_models.py tests/test_douyin_manifest.py
git commit -m "feat: persist Douyin eligibility decisions"
```

### Task 3: Filter before acquisition and make completion policy-aware

**Files:**
- Modify: `src/pkb/douyin/pipeline.py`
- Modify: `src/pkb/douyin/live.py`
- Modify: `tests/test_douyin_pipeline.py`
- Modify: `tests/test_acceptance_douyin.py`

- [ ] **Step 1: Write failing pre-acquisition tests**

Inject a fake classifier and prove excluded items never call `prepare`,
`acquire`, `extract_audio`, or `transcribe`; kept items retain existing resume
behavior. Assert audits report `classified`, `eligible`, `excluded`, and reason
counts without private metadata. Prove classifier errors stop publication
rather than silently excluding items.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_douyin_pipeline.py tests/test_acceptance_douyin.py -q`
Expected: FAIL because the pipeline has no classifier boundary.

- [ ] **Step 3: Implement classify-then-process orchestration**

Add a classifier dependency to `DouyinPipeline`. Refresh stale decisions before
constructing its processing list. Count decisions separately from processing
stages. Process only current `keep` decisions. Wire the production rule
classifier in `_build_pipeline`; keep explicit dependency injection for tests.
Treat classifier exceptions as a safe run-stopping `classification_failed`.

- [ ] **Step 4: Verify GREEN and regression tests**

Run: `python -m pytest tests/test_douyin_pipeline.py tests/test_acceptance_douyin.py tests/test_douyin_media.py tests/test_douyin_transcription.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/douyin/pipeline.py src/pkb/douyin/live.py tests/test_douyin_pipeline.py tests/test_acceptance_douyin.py
git commit -m "feat: filter Douyin videos before acquisition"
```

### Task 4: Rebuild the existing corpus safely

**Files:**
- Create: `src/pkb/douyin/rebuild.py`
- Create: `tests/test_douyin_rebuild.py`
- Modify: `src/pkb/douyin/live.py`

- [ ] **Step 1: Write failing atomic rebuild tests**

Create mixed keep/exclude source records and prove the rebuilt JSONL contains
only unique kept IDs, preserves complete accepted records byte-for-byte, creates
one timestamped backup before canonical replacement, ignores backup files during
indexing, and leaves the canonical file untouched if validation fails.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_douyin_rebuild.py -q`
Expected: FAIL because the rebuild module does not exist.

- [ ] **Step 3: Implement atomic corpus filtering**

Implement `rebuild_filtered_corpus(raw_path, manifest, backup_root, now)` using
an adjacent temporary JSONL, fsync, validation against current keep decisions,
timestamped backup via `shutil.copy2`, and `Path.replace`. Never rewrite the
manifest's discovered metadata or remove the backup.

- [ ] **Step 4: Integrate once-per-policy rebuild**

Before processing, filter existing raw records after all current decisions are
known. Record the applied classifier version in a small ignored state marker so
ordinary resumes do not create repeated backups.

- [ ] **Step 5: Verify GREEN and index isolation**

Run: `python -m pytest tests/test_douyin_rebuild.py tests/test_knowledge_indexer.py tests/test_acceptance_douyin.py -q`
Expected: all tests pass and backup JSONL is not indexed.

- [ ] **Step 6: Commit**

```powershell
git add src/pkb/douyin/rebuild.py src/pkb/douyin/live.py tests/test_douyin_rebuild.py tests/test_acceptance_douyin.py
git commit -m "feat: rebuild filtered Douyin corpus safely"
```

### Task 5: Expose policy controls and document operations

**Files:**
- Modify: `src/pkb/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `docs/second-brain-operations.md`
- Modify: `tests/test_operations_documentation.py`

- [ ] **Step 1: Write failing CLI and documentation tests**

Assert `--reclassify` reaches the full runner, bounded/full syncs use filtering
by default, aggregate output includes policy counts but not titles/authors, and
the guide explains default exclusion, backup, resumption, and validation.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_cli.py tests/test_operations_documentation.py -q`
Expected: FAIL because `--reclassify` and policy documentation are absent.

- [ ] **Step 3: Implement the CLI contract and guide**

Add `--reclassify`, pass it through `run_douyin_full`, print safe aggregate
counts, and block index/Wiki refresh when kept retryable items remain. Document
the exact filtered full-sync and recovery commands plus backup semantics.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_cli.py tests/test_operations_documentation.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/cli.py tests/test_cli.py docs/second-brain-operations.md tests/test_operations_documentation.py
git commit -m "feat: expose knowledge-first Douyin sync"
```

### Task 6: Verify and rerun real filtered synchronization

**Files:**
- Runtime data only under ignored `data/`

- [ ] **Step 1: Run all automated verification**

Run: `python -m pytest -q --basetemp .pytest-tmp-knowledge-filter-final`
Expected: all tests pass with only documented platform skips.

Run: `ruff check .`
Expected: `All checks passed!`.

- [ ] **Step 2: Run a real classification/rebuild sync**

Run: `python -m pkb.cli export douyin-favorites --all --reclassify --request-delay 7`
Expected: all discovered items receive current decisions; excluded items incur
no media acquisition; kept items resume through cleaned/unavailable states.

- [ ] **Step 3: Validate aggregate state without exposing content**

Report only discovered/classified/eligible/excluded counts, reason-code counts,
raw line count, unique ID count, non-empty transcript count, unfinished kept
count, cleanup pending, backup existence, and index/Wiki refresh flags. Require
excluded/raw intersection to be empty.

- [ ] **Step 4: Record repository state**

Run: `git status --short && git log -8 --oneline`
Expected: no implementation files remain uncommitted; only existing ignored or
untracked pytest temporary directories may remain.
