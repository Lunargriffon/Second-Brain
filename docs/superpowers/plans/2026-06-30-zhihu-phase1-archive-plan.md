# Zhihu Phase 1 Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably archive Zhihu collection text, links, metadata, and later images into local resumable files.

**Architecture:** Keep the current simple pipeline: CLI -> Exporter -> ZhihuClient -> JSONL. Add validation and reporting around the pipeline before expanding to image download. Image archiving is a separate resumable stage and must not block or corrupt text exports.

**Tech Stack:** Python 3.11+, standard library, pytest, local JSONL, local checkpoint JSON.

---

### Task 1: Freeze Text Archive Contract

**Files:**
- Modify: `schema/article.v1.json`
- Modify: `src/pkb/schema.py`
- Test: `tests/test_schema_validation.py`

- [ ] **Step 1: Write failing tests**

Add tests proving raw articles include stable source metadata:

```python
def test_validate_article_accepts_source_collection_metadata():
    article = valid_article()
    article["source_collection_id"] = "575638886"
    article["source_collection_title"] = "恋爱"
    validate_article(article)
```

- [ ] **Step 2: Verify failure**

Run:

```bash
python -m pytest tests/test_schema_validation.py -v
```

Expected: fails because additional fields are not allowed.

- [ ] **Step 3: Update schema**

Add optional fields:

```json
"source_collection_id": {"type": "string"},
"source_collection_title": {"type": "string"}
```

- [ ] **Step 4: Verify pass**

Run:

```bash
python -m pytest tests/test_schema_validation.py -v
```

Expected: pass.

---

### Task 2: Improve Collection Audit Output

**Files:**
- Modify: `src/pkb/audit.py`
- Modify: `src/pkb/cli.py`
- Test: `tests/test_audit.py`
- Test: `tests/test_batch_cli.py`

- [ ] **Step 1: Write failing tests**

Assert audit can write a machine-readable JSONL report:

```python
def test_audit_result_can_be_serialized_for_report():
    result = AuditResult(
        collection_id="575638886",
        reported_total=134,
        scanned_total=134,
        type_counts={"answer": 125, "article": 9},
        page_count=7,
        warnings=[],
    )
    assert result.to_dict()["collection_id"] == "575638886"
```

- [ ] **Step 2: Implement minimal serializer**

Add `to_dict()` to `AuditResult`.

- [ ] **Step 3: Add CLI option**

Add:

```bash
pkb audit zhihu --collections-file data/config/zhihu-collections.txt --report data/state/zhihu-audit.jsonl
```

- [ ] **Step 4: Verify**

Run:

```bash
python -m pytest tests/test_audit.py tests/test_batch_cli.py -v
```

Expected: pass.

---

### Task 3: Full Text Export With Per-Collection Isolation

**Files:**
- Modify: `src/pkb/cli.py`
- Modify: `src/pkb/exporter.py`
- Test: `tests/test_batch_cli.py`
- Test: `tests/test_exporter.py`

- [ ] **Step 1: Write failing tests**

Assert batch export keeps each collection isolated:

```python
def test_batch_export_writes_one_jsonl_and_state_per_collection(tmp_path):
    # Given two collection URLs
    # Expect raw/zhihu-<id>.jsonl and state/zhihu-<id>.state.json for each
```

- [ ] **Step 2: Verify failure**

Run:

```bash
python -m pytest tests/test_batch_cli.py -v
```

- [ ] **Step 3: Implement only needed behavior**

Keep current naming:

```text
data/raw/zhihu-<collection_id>.jsonl
data/state/zhihu-<collection_id>.state.json
```

- [ ] **Step 4: Run real low-frequency export**

Start with:

```powershell
$env:PYTHONPATH='src'
python -m pkb.cli export zhihu-batch `
  --collections-file data/config/zhihu-collections.txt `
  --output-dir data/raw `
  --state-dir data/state `
  --limit 0 `
  --request-delay 2
```

Expected: one JSONL per collection. `--limit 0` means no local item cap.

---

### Task 4: Post-Export Verification

**Files:**
- Create: `src/pkb/verify.py`
- Modify: `src/pkb/cli.py`
- Test: `tests/test_verify.py`

- [ ] **Step 1: Write failing tests**

Assert verifier compares audit counts to exported JSONL line counts:

```python
def test_verify_reports_count_match(tmp_path):
    # audit says scanned_total=2
    # jsonl has 2 valid lines
    # verifier reports status ok
```

- [ ] **Step 2: Implement verifier**

Add:

```bash
pkb verify zhihu --audit data/state/zhihu-audit.jsonl --raw-dir data/raw
```

It should report:

```text
575638886 ok scanned=134 exported=134
```

- [ ] **Step 3: Run verification after export**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pkb.cli verify zhihu --audit data/state/zhihu-audit.jsonl --raw-dir data/raw
```

Expected: most collections match; mismatches are listed explicitly.

---

### Task 5: Add Image URL Extraction Only

**Files:**
- Modify: `src/pkb/zhihu.py`
- Test: `tests/test_real_zhihu_client.py`

- [ ] **Step 1: Write failing tests**

Assert HTML image URLs are extracted into `images` but not downloaded:

```python
def test_real_zhihu_client_extracts_image_urls_without_downloading():
    # answer content has <img src="https://...jpg">
    # article["images"] contains that URL
```

- [ ] **Step 2: Implement extraction**

Use `HTMLParser` or a small standard-library parser extension. Do not download images.

- [ ] **Step 3: Verify**

Run:

```bash
python -m pytest tests/test_real_zhihu_client.py -v
```

Expected: pass.

---

### Task 6: Separate Resumable Image Downloader

**Files:**
- Create: `src/pkb/images.py`
- Modify: `src/pkb/cli.py`
- Test: `tests/test_images.py`

- [ ] **Step 1: Write failing tests**

Assert downloader reads JSONL image URLs and writes separate state:

```python
def test_image_downloader_uses_separate_checkpoint(tmp_path):
    # raw jsonl has image URLs
    # downloader writes images and data/state/images-<collection_id>.state.json
```

- [ ] **Step 2: Implement conservative downloader**

Rules:

```text
default disabled
single-threaded
request-delay >= 2
max images per run
403/429 stops immediately
state separate from article checkpoint
```

- [ ] **Step 3: Add CLI**

Add:

```bash
pkb images zhihu --raw data/raw/zhihu-575638886.jsonl --output-dir data/images/zhihu-575638886 --limit 20
```

- [ ] **Step 4: Verify**

Run:

```bash
python -m pytest tests/test_images.py -v
```

Expected: pass.

---

### Task 7: Final Safety Check

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document safe workflow**

Add:

```text
1. Audit collections
2. Export text JSONL
3. Verify counts
4. Extract image URLs
5. Download images separately only when needed
```

- [ ] **Step 2: Run full tests**

Run:

```bash
python -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 3: Real data acceptance criteria**

Accept Phase 1 text archive when:

```text
all collection URLs processed
each collection has its own JSONL
each collection has its own checkpoint
schema validation passes for every line
verify report explains all mismatches
no image downloads were required for text archive
```
