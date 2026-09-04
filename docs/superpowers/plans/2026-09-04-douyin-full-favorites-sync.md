# Douyin Full Favorites Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit, resumable `--all` mode that discovers and transcribes every available Douyin favorite, then refreshes the knowledge index and Wiki.

**Architecture:** Keep the existing bounded collector and trial command unchanged. Add a full collector that checkpoints each browser observation into the existing manifest, stops after three consecutive no-growth observations, and then feeds the existing resumable media pipeline. The CLI owns post-sync index and Wiki orchestration so the Douyin domain module stays independent of other application subsystems.

**Tech Stack:** Python 3.12, argparse, OpenCLI browser automation, pytest, SQLite knowledge index, Markdown Wiki exporter.

---

### Task 1: Full-library discovery with incremental checkpoints

**Files:**
- Modify: `src/pkb/douyin/collector.py`
- Modify: `tests/test_douyin_collector.py`

- [ ] **Step 1: Write failing full-discovery tests**

Add tests that prove full discovery waits for three consecutive no-growth observations, deduplicates cumulative browser results, checkpoints every newly discovered batch, and retains the 5–10 second delay contract:

```python
def test_collect_all_stops_after_three_no_growth_observations():
    browser = FakeFavoritesBrowser(pages=[
        page([1, 2], cursor="2"),
        page([1, 2, 3], cursor="3"),
        page([1, 2, 3], cursor=None),
        page([1, 2, 3], cursor=None),
        page([1, 2, 3], cursor=None),
    ])
    batches = []
    delays = []
    result = FavoritesCollector(
        browser, delay=delays.append, request_delay=7
    ).collect_all(on_discovered=lambda items: batches.append(items))
    assert [item.work_id for item in result] == ["1", "2", "3"]
    assert [[item.work_id for item in batch] for batch in batches] == [["1", "2"], ["3"]]
    assert browser.page_calls == 5
    assert delays == [7, 7, 7, 7]


def test_collect_all_deduplicates_ids_already_in_manifest():
    browser = FakeFavoritesBrowser(pages=[page([1, 2]), page([1, 2]), page([1, 2])])
    batches = []
    result = FavoritesCollector(browser, delay=lambda _: None).collect_all(
        known_ids={"1"}, on_discovered=lambda items: batches.append(items)
    )
    assert [item.work_id for item in result] == ["2"]
    assert [[item.work_id for item in batch] for batch in batches] == [["2"]]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_douyin_collector.py -q`

Expected: FAIL because `FavoritesCollector.collect_all` does not exist.

- [ ] **Step 3: Implement the minimal full collector**

Add this API while leaving `collect(limit=20)` intact:

```python
def collect_all(
    self,
    *,
    known_ids: set[str] | None = None,
    on_discovered: Callable[[list[FavoriteItem]], None] | None = None,
    stable_observations: int = 3,
) -> list[FavoriteItem]:
    if stable_observations < 1:
        raise ValueError("stable observations must be positive")
    seen = set(known_ids or ())
    discovered: list[FavoriteItem] = []
    cursor: str | None = None
    unchanged = 0
    while unchanged < stable_observations:
        page = self.browser.page(cursor)
        batch: list[FavoriteItem] = []
        for raw in page.items:
            item = _normalize(raw, observed_at=page.observed_at)
            if item.work_id not in seen:
                seen.add(item.work_id)
                batch.append(item)
        if batch:
            discovered.extend(batch)
            unchanged = 0
            if on_discovered is not None:
                on_discovered(batch)
        else:
            unchanged += 1
        if unchanged < stable_observations:
            cursor = page.cursor or str(len(seen))
            self.delay(self.request_delay)
    return discovered
```

- [ ] **Step 4: Verify collector tests pass**

Run: `python -m pytest tests/test_douyin_collector.py -q`

Expected: all collector tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/douyin/collector.py tests/test_douyin_collector.py
git commit -m "feat: discover complete Douyin favorites collection"
```

### Task 2: Compose resumable full discovery and processing

**Files:**
- Modify: `src/pkb/douyin/live.py`
- Modify: `tests/test_acceptance_douyin.py`

- [ ] **Step 1: Write a failing full-run composition test**

Use injected browser and pipeline collaborators so the test remains offline. Verify existing manifest IDs are supplied to discovery, every new batch is persisted immediately, and the pipeline runs only after discovery reaches its stable end:

```python
def test_full_run_checkpoints_discovery_before_processing(tmp_path, monkeypatch):
    events = []
    browser = FakeFavoritesBrowserForLive([["1", "2"], ["1", "2", "3"], ["1", "2", "3"]])
    manifest = ManifestStore(tmp_path / "state.json")
    manifest.discover([favorite("1")])
    audit = run_live_full(
        output=tmp_path / "raw.jsonl",
        state=tmp_path / "state.json",
        report=tmp_path / "audit.json",
        temp_root=tmp_path / "media",
        request_delay=7,
        browser=browser,
        pipeline_factory=lambda store: RecordingPipeline(store, events),
        delay=lambda _: None,
    )
    assert events == [("pipeline", ["1", "2", "3"])]
    assert [item.work_id for item in manifest.items()] == ["1", "2", "3"]
    assert audit.discovery_complete is True
    assert audit.discovered == 2
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_acceptance_douyin.py::test_full_run_checkpoints_discovery_before_processing -q`

Expected: FAIL because `run_live_full` and the full audit fields do not exist.

- [ ] **Step 3: Add full-run audit and composition**

Add a focused immutable result type and reuse the existing pipeline construction:

```python
@dataclass(frozen=True)
class FullRunAudit:
    counts: dict[str, int]
    errors: dict[str, int]
    stopped: bool
    cleanup_pending: int
    discovered: int
    discovery_complete: bool
    index_refreshed: bool = False
    wiki_refreshed: bool = False


def run_live_full(*, output: Path, state: Path, report: Path, temp_root: Path,
                  request_delay: float, browser: FavoritesBrowser | None = None,
                  delay: Callable[[float], None] = time.sleep,
                  pipeline_factory: Callable[[ManifestStore], DouyinPipeline] | None = None) -> FullRunAudit:
    manifest = ManifestStore(state)
    known = {item.work_id for item in manifest.items()}
    collector = FavoritesCollector(browser or OpenCliFavoritesBrowser(), delay=delay,
                                   request_delay=request_delay)
    try:
        discovered = collector.collect_all(known_ids=known, on_discovered=manifest.discover)
        pipeline = pipeline_factory(manifest) if pipeline_factory else _build_pipeline(
            manifest, output, temp_root
        )
        run = pipeline.run()
        audit = FullRunAudit(run.counts, run.errors, run.stopped,
                             run.cleanup_pending, len(discovered), True)
    except CollectionStopped as exc:
        audit = FullRunAudit({"selected": 0}, {exc.code: 1}, True, 0, 0, False)
    write_audit_atomic(report, asdict(audit))
    return audit
```

Extract `_build_pipeline()` from the existing `run_live_trial()` composition so trial and full mode use the same downloader, transcriber, media storage, and probe implementations.

- [ ] **Step 4: Test trial and full compositions together**

Run: `python -m pytest tests/test_acceptance_douyin.py tests/test_douyin_pipeline.py -q`

Expected: PASS, including all original trial recovery tests.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/douyin/live.py tests/test_acceptance_douyin.py
git commit -m "feat: compose resumable full Douyin sync"
```

### Task 3: Add explicit full-mode CLI contract

**Files:**
- Modify: `src/pkb/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI selection tests**

```python
def test_douyin_all_selects_full_runner(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        "pkb.cli.run_douyin_full",
        lambda **kwargs: captured.update(kwargs) or full_audit(),
    )
    assert main(["export", "douyin-favorites", "--all", "--state", str(tmp_path / "s.json")]) == 0
    assert captured["request_delay"] == 7


def test_douyin_all_and_limit_are_mutually_exclusive():
    assert main(["export", "douyin-favorites", "--all", "--limit", "5"]) == 2
```

Retain the existing test that no flag invokes the 20-item trial.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_cli.py -q`

Expected: FAIL because argparse does not recognize `--all` and no full runner wrapper exists.

- [ ] **Step 3: Implement mutually exclusive mode selection**

Replace the current limit argument with a group that preserves the default only after parsing:

```python
mode = douyin_parser.add_mutually_exclusive_group()
mode.add_argument("--all", action="store_true")
mode.add_argument("--limit", type=int, choices=range(1, 21))
douyin_parser.set_defaults(limit=20)
```

Add `run_douyin_full()` as a lazy-import wrapper around `pkb.douyin.live.run_live_full`, and branch in `_run_douyin_favorites()` on `args.all`. Keep all output fields sanitized and add `discovered` and `discovery_complete` only for full mode.

- [ ] **Step 4: Run CLI and Douyin tests**

Run: `python -m pytest tests/test_cli.py tests/test_acceptance_douyin.py tests/test_douyin_collector.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/cli.py tests/test_cli.py
git commit -m "feat: expose explicit full Douyin sync mode"
```

### Task 4: Refresh index and Wiki after a successful full sync

**Files:**
- Modify: `src/pkb/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_douyin_all_refreshes_index_and_wiki_after_success(monkeypatch):
    calls = []
    monkeypatch.setattr("pkb.cli.run_douyin_full", lambda **_: full_audit(stopped=False))
    monkeypatch.setattr("pkb.cli._refresh_douyin_outputs", lambda: calls.append("refresh") or (True, True))
    assert main(["export", "douyin-favorites", "--all"]) == 0
    assert calls == ["refresh"]


def test_douyin_all_does_not_refresh_after_run_stopper(monkeypatch):
    calls = []
    monkeypatch.setattr("pkb.cli.run_douyin_full", lambda **_: full_audit(stopped=True))
    monkeypatch.setattr("pkb.cli._refresh_douyin_outputs", lambda: calls.append("refresh"))
    assert main(["export", "douyin-favorites", "--all"]) == 1
    assert calls == []
```

Also test that either post-processing failure returns 1 without deleting raw data and exposes only `index_refresh_failed` or `wiki_refresh_failed`.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_cli.py -q`

Expected: FAIL because `_refresh_douyin_outputs` does not exist.

- [ ] **Step 3: Add post-processing orchestration**

Implement `_refresh_douyin_outputs()` by invoking the existing internal command handlers with fixed local paths equivalent to:

```python
index_code = main([
    "index", "build", "--raw-dir", "data/raw",
    "--db", "data/index/knowledge.db", "--strict",
])
wiki_code = main([
    "wiki", "export", "--db", "data/index/knowledge.db",
    "--vault", "vault", "--report", "data/state/wiki-export-report.json",
]) if index_code == 0 else 1
return index_code == 0, wiki_code == 0
```

Update the full audit atomically after refresh so `index_refreshed` and `wiki_refreshed` reflect the final state. Do not run refresh for bounded trial mode or a stopped full run.

- [ ] **Step 4: Verify orchestration tests**

Run: `python -m pytest tests/test_cli.py tests/test_knowledge_cli.py tests/test_wiki_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/cli.py tests/test_cli.py
git commit -m "feat: refresh knowledge outputs after full Douyin sync"
```

### Task 5: Document, verify, and run the complete collection

**Files:**
- Modify: `docs/second-brain-operations.md`
- Modify: `tests/test_operations_documentation.py`

- [ ] **Step 1: Add a failing documentation contract test**

```python
def test_operations_guide_covers_full_douyin_sync_and_recovery():
    text = OPERATIONS.read_text(encoding="utf-8")
    assert "pkb export douyin-favorites --all --request-delay 7" in text
    assert "three consecutive" in text
    assert "does not delete existing raw records" in text
```

- [ ] **Step 2: Run the documentation test and verify RED**

Run: `python -m pytest tests/test_operations_documentation.py -q`

Expected: FAIL because the operations guide documents only the bounded trial.

- [ ] **Step 3: Document the full command and recovery semantics**

Add the exact full command, explain the three-observation end condition, state that existing records are preserved, and show that rerunning the same command resumes failed or interrupted work:

```powershell
$env:PYTHONPATH='src'
python -m pkb.cli export douyin-favorites --all --request-delay 7
```

- [ ] **Step 4: Run all automated verification**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
ruff check .
```

Expected: all tests PASS with only the existing Windows symlink skips; Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit documentation**

```powershell
git add docs/second-brain-operations.md tests/test_operations_documentation.py
git commit -m "docs: explain full Douyin favorites sync"
```

- [ ] **Step 6: Run the live full sync**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pkb.cli export douyin-favorites --all --request-delay 7
```

Expected: discovery reaches its stable end, already cleaned works are skipped, all processable works reach `cleaned`, `cleanup_pending=0`, and index/Wiki refresh flags are true. If authentication, CAPTCHA, 403, or 429 stops the run, preserve the manifest and rerun the identical command after the browser session is healthy.

- [ ] **Step 7: Validate aggregate state without exposing content**

Run a local validation that reports only line counts, unique work IDs, manifest stage counts, and the two refresh flags. Require raw work IDs to be unique, every raw transcript to be non-empty, and every non-unavailable manifest item to be cleaned.

- [ ] **Step 8: Record final repository state**

Run:

```powershell
git status --short
git log -5 --oneline
```

Expected: only pre-existing ignored/untracked test temporary directories may remain; no implementation files are uncommitted.
