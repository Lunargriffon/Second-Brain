# OpenCLI Browser-Session Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in, cookie-free Zhihu and Douyin synchronization through the authenticated OpenCLI Chrome session while preserving every existing path and data file.

**Architecture:** A focused OpenCLI gateway runs read-only commands, decodes UTF-8 JSON, and exposes safe typed results. Douyin uses persisted browser network captures only when its existing downloader cannot read Chrome cookies; Zhihu adds a separate browser-backed client selected explicitly by CLI option. Existing HTTP clients, raw schemas, checkpoints, limits, and cleanup remain unchanged.

**Tech Stack:** Python 3.12, subprocess, dataclasses, OpenCLI browser bridge, yt-dlp, pytest, SQLite.

---

## File Structure

- Create `src/pkb/opencli_gateway.py`: UTF-8 subprocess boundary and safe JSON validation.
- Modify `src/pkb/douyin/live.py`: capture `aweme/detail` and download an HTTPS play URL when the direct element source is `blob:`.
- Create `src/pkb/zhihu_opencli.py`: browser-backed collection/detail client implementing the existing `ZhihuClient` protocol.
- Modify `src/pkb/cli.py`: add explicit `--browser-session` selection without removing Cookie behavior.
- Add focused tests and update operations documentation.

### Task 1: Safe OpenCLI Gateway

**Files:**
- Create: `src/pkb/opencli_gateway.py`
- Create: `tests/test_opencli_gateway.py`

- [ ] **Step 1: Write failing gateway tests**

Test that `OpenCliGateway.run_json(["zhihu", "whoami", "-f", "json"])` resolves `opencli` with `shutil.which`, passes `encoding="utf-8"` and `errors="replace"`, returns mappings/lists, and maps missing executables, non-zero exits, and malformed JSON to `OpenCliError` safe codes without embedding stdout/stderr.

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_opencli_gateway.py -q`
Expected: import failure because `pkb.opencli_gateway` does not exist.

- [ ] **Step 3: Implement the gateway**

Implement `OpenCliError(code)`, `OpenCliGateway(runner=subprocess.run, executable=None)`, and `run_json(arguments)` with a 60-second timeout. Accept only a JSON mapping or list; never include response text in exceptions.

- [ ] **Step 4: Verify and commit**

Run: `pytest tests/test_opencli_gateway.py -q && ruff check src/pkb/opencli_gateway.py tests/test_opencli_gateway.py`
Expected: all pass.

Commit: `feat: add safe OpenCLI gateway`

### Task 2: Douyin Blob-Media Browser Fallback

**Files:**
- Modify: `src/pkb/douyin/live.py`
- Modify: `tests/test_acceptance_douyin.py`

- [ ] **Step 1: Write failing response-capture tests**

Extend the fake runner to require this sequence: open a stable browser tab, call `browser <session> network` to arm capture, navigate that same tab to the work URL, read `browser <session> network` again, request `--detail` for the newest `aweme/detail` key, and select an HTTPS URL from `aweme_detail.video.play_addr.url_list`. Assert a `Referer:<work-url>` header is supplied to yt-dlp. Add malformed, missing, non-HTTPS, timeout, and subprocess-encoding cases.

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_acceptance_douyin.py -q`
Expected: blob-response test fails with `media_url_unavailable`.

- [ ] **Step 3: Implement minimal capture fallback**

Keep the existing cookie and direct-element attempts first. Add a bounded browser capture method using `OpenCliGateway`: arm capture, navigate the same tab, poll at most 15 seconds, choose only the newest matching detail response for the requested work ID, validate HTTPS, then invoke yt-dlp with Referer. Emit only stable safe codes.

- [ ] **Step 4: Verify and commit**

Run: `pytest tests/test_acceptance_douyin.py tests/test_douyin_pipeline.py tests/test_douyin_media.py -q && ruff check src tests`
Expected: all pass.

Commit: `fix: acquire Douyin blob media from browser capture`

### Task 3: Browser-Backed Zhihu Sync and Acceptance

**Files:**
- Create: `src/pkb/zhihu_opencli.py`
- Modify: `src/pkb/cli.py`
- Create: `tests/test_zhihu_opencli.py`
- Modify: `tests/test_cli.py`
- Modify: `docs/second-brain-operations.md`

- [ ] **Step 1: Write failing client and CLI tests**

Test collection pagination with offsets and page size 20, answer detail normalization through the existing normalized record contract, deduplication, and stable stop codes. Test `pkb export zhihu-batch --browser-session` selects the new client while the command without that flag still selects `RealZhihuClient` and `.env` Cookie behavior.

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_zhihu_opencli.py tests/test_cli.py -q`
Expected: import/argument failures because the browser client and flag do not exist.

- [ ] **Step 3: Implement the explicit browser client**

Implement `OpenCliZhihuClient.iter_collection()` using `zhihu collection <id> --offset N --limit 20 -f json`; resolve answers with `zhihu answer-detail <id> -f json`; normalize IDs, title, URL, author, HTML/text content, timestamps, and images into the same dictionaries returned by `RealZhihuClient`. Stop when a short/empty page is returned or the configured item limit is reached. Unsupported item types receive a stable explicit error rather than partial persistence.

- [ ] **Step 4: Add CLI selection and operations instructions**

Add `--browser-session` to `zhihu`, `zhihu-author`, and `zhihu-batch`. When set, require OpenCLI `whoami.logged_in=true`; otherwise preserve the existing Cookie client. Document that Chrome and the OpenCLI extension must remain open.

- [ ] **Step 5: Verify full behavior**

Run: `pytest tests/test_zhihu_opencli.py tests/test_cli.py tests/test_exporter.py -q && pytest -q && ruff check src tests`
Expected: full suite passes with no network required.

- [ ] **Step 6: Live bounded acceptance and projections**

Run browser-backed Zhihu sync to separate probe output first, compare its first normalized item with the isolated probe, then run the normal collection sync. Resume the remaining Douyin 20-item trial, require `cleanup_pending=0`, rebuild `data/index/knowledge.db`, and export the Vault. Do not delete or overwrite old raw files until the new outputs validate.

- [ ] **Step 7: Commit**

Commit: `feat: sync favorites through authenticated browser session`
