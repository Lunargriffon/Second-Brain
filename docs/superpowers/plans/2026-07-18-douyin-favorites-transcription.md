# Douyin Favorites Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process the 20 most recent authenticated Douyin favorites into searchable transcript documents while deleting all temporary media after durable persistence.

**Architecture:** A replaceable browser collector emits a resumable manifest; a single-item pipeline acquires media, extracts audio, runs SenseVoice with a faster-whisper fallback, appends validated JSONL, and cleans its dedicated temporary directory. A new source adapter feeds those records into the existing repository, FTS, derivation, and wiki projections.

**Tech Stack:** Python 3.11+, stdlib dataclasses/JSON/subprocess/pathlib, Playwright over the existing Chrome session adapter, FFmpeg, FunASR/SenseVoice, faster-whisper CPU INT8, pytest, SQLite FTS.

---

## File Structure

- Create `src/pkb/douyin/models.py`: manifest entries, transcript segments, raw records, status transitions.
- Create `src/pkb/douyin/manifest.py`: atomic checkpoint persistence and resumable claims.
- Create `src/pkb/douyin/collector.py`: collector protocol, browser result normalization, stop conditions.
- Create `src/pkb/douyin/media.py`: bounded media acquisition and FFmpeg audio extraction.
- Create `src/pkb/douyin/transcription.py`: engine protocols, SenseVoice primary, faster-whisper fallback, quality gate.
- Create `src/pkb/douyin/pipeline.py`: one-work orchestration, durable JSONL append, cleanup, audit.
- Create `src/pkb/sources/douyin_favorites.py`: raw-record normalization into `NormalizedDocument`.
- Modify `src/pkb/knowledge/indexer.py`: route `douyin-favorites*.jsonl`.
- Modify `src/pkb/cli.py`: add bounded `export douyin-favorites` entry point.
- Modify `pyproject.toml`: add isolated `douyin` optional dependencies.
- Create fixtures and focused tests under `tests/fixtures/douyin/` and `tests/test_douyin_*.py`.
- Modify `docs/second-brain-operations.md`: trial, resume, cleanup, and index commands.

### Task 1: Domain Models and Legal State Transitions

**Files:**
- Create: `src/pkb/douyin/__init__.py`
- Create: `src/pkb/douyin/models.py`
- Test: `tests/test_douyin_models.py`

- [ ] **Step 1: Write failing transition and serialization tests**

```python
from dataclasses import replace
import pytest
from pkb.douyin.models import FavoriteItem, Stage, TranscriptSegment

def item() -> FavoriteItem:
    return FavoriteItem(work_id="7", url="https://www.douyin.com/video/7", author_id="u1",
        author="作者", caption="内容", hashtags=("知识",), published_at=None,
        observed_at="2026-07-18T00:00:00Z", stage=Stage.DISCOVERED)

def test_stage_transition_is_explicit_and_serializable():
    acquired = item().transition(Stage.ACQUIRED)
    assert acquired.to_dict()["stage"] == "acquired"
    assert FavoriteItem.from_dict(acquired.to_dict()) == acquired

def test_illegal_transition_is_rejected():
    with pytest.raises(ValueError, match="discovered -> indexed"):
        item().transition(Stage.INDEXED)

def test_segment_rejects_reverse_time():
    with pytest.raises(ValueError, match="end"):
        TranscriptSegment(start=2.0, end=1.0, text="bad")
```

- [ ] **Step 2: Run tests and verify red**

Run: `pytest tests/test_douyin_models.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'pkb.douyin'`.

- [ ] **Step 3: Implement immutable models and transition table**

```python
class Stage(str, Enum):
    DISCOVERED="discovered"; ACQUIRED="acquired"; AUDIO_READY="audio_ready"
    TRANSCRIBED="transcribed"; PERSISTED="persisted"; INDEXED="indexed"
    CLEANED="cleaned"; FAILED="failed"; UNAVAILABLE="unavailable"

_NEXT = {
    Stage.DISCOVERED: {Stage.ACQUIRED, Stage.UNAVAILABLE, Stage.FAILED},
    Stage.ACQUIRED: {Stage.AUDIO_READY, Stage.FAILED},
    Stage.AUDIO_READY: {Stage.TRANSCRIBED, Stage.FAILED},
    Stage.TRANSCRIBED: {Stage.PERSISTED, Stage.FAILED},
    Stage.PERSISTED: {Stage.INDEXED, Stage.CLEANED},
    Stage.INDEXED: {Stage.CLEANED}, Stage.FAILED: {Stage.DISCOVERED},
    Stage.CLEANED: set(), Stage.UNAVAILABLE: set(),
}
```

Define frozen `TranscriptSegment`, `FavoriteItem`, and `DouyinRawRecord`; validate IDs, HTTPS URLs, timestamps, segment order, and ensure `DouyinRawRecord.to_dict()` contains no local path fields.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_douyin_models.py -q`

Expected: `3 passed`.

```powershell
git add src/pkb/douyin tests/test_douyin_models.py
git commit -m "feat: add Douyin ingestion domain models"
```

### Task 2: Atomic Resumable Manifest

**Files:**
- Create: `src/pkb/douyin/manifest.py`
- Test: `tests/test_douyin_manifest.py`

- [ ] **Step 1: Write failing persistence and recovery tests**

```python
def test_manifest_round_trip_and_idempotent_discovery(tmp_path):
    store = ManifestStore(tmp_path / "state.json")
    store.discover([item("1"), item("1"), item("2")])
    assert [x.work_id for x in ManifestStore(store.path).items()] == ["1", "2"]

def test_manifest_atomic_write_preserves_old_file_on_replace_failure(tmp_path, monkeypatch):
    store = ManifestStore(tmp_path / "state.json"); store.discover([item("1")])
    monkeypatch.setattr(Path, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError): store.update("1", Stage.ACQUIRED)
    assert ManifestStore(store.path).get("1").stage is Stage.DISCOVERED
```

- [ ] **Step 2: Run tests and verify red**

Run: `pytest tests/test_douyin_manifest.py -q`

Expected: FAIL because `ManifestStore` is undefined.

- [ ] **Step 3: Implement atomic store**

```python
class ManifestStore:
    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps({"version": 1, "items": [x.to_dict() for x in items]},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
```

Keep insertion order, deduplicate by `work_id`, expose `pending()`, `get()`, `discover()`, and checked `update()`.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_douyin_manifest.py -q`

Expected: all tests PASS.

```powershell
git add src/pkb/douyin/manifest.py tests/test_douyin_manifest.py
git commit -m "feat: add resumable Douyin manifest"
```

### Task 3: Bounded Favorites Collector

**Files:**
- Create: `src/pkb/douyin/collector.py`
- Create: `tests/fixtures/douyin/favorites-page.json`
- Test: `tests/test_douyin_collector.py`

- [ ] **Step 1: Write fake-browser tests**

```python
def test_collects_first_twenty_unique_items_and_stops():
    browser = FakeFavoritesBrowser(pages=[page(range(1, 16)), page(range(10, 31))])
    result = FavoritesCollector(browser, delay=lambda _: None).collect(limit=20)
    assert [x.work_id for x in result] == [str(i) for i in range(1, 21)]
    assert browser.page_calls == 2

@pytest.mark.parametrize("code", ["auth_required", "captcha", "http_403", "http_429"])
def test_challenge_stops_without_fetching_next_page(code):
    browser = FakeFavoritesBrowser(error=CollectionStopped(code))
    with pytest.raises(CollectionStopped, match=code): FavoritesCollector(browser).collect(limit=20)
    assert browser.page_calls == 1
```

- [ ] **Step 2: Run tests and verify red**

Run: `pytest tests/test_douyin_collector.py -q`

Expected: FAIL because collector types are missing.

- [ ] **Step 3: Implement protocol and bounded normalization**

```python
class FavoritesBrowser(Protocol):
    def page(self, cursor: str | None) -> FavoritePage: ...

class FavoritesCollector:
    def collect(self, *, limit: int = 20) -> list[FavoriteItem]:
        if limit < 1 or limit > 20: raise ValueError("trial limit must be 1..20")
        # page, normalize, deduplicate, delay 5..10 seconds, stop at limit/no cursor
```

The concrete browser implementation must consume structured results from the authenticated browser helper; do not parse cookies or log response bodies. Map 403/429/login/CAPTCHA into `CollectionStopped` codes.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_douyin_collector.py -q`

Expected: all tests PASS.

```powershell
git add src/pkb/douyin/collector.py tests/fixtures/douyin tests/test_douyin_collector.py
git commit -m "feat: collect bounded Douyin favorites"
```

### Task 4: Safe Temporary Media and FFmpeg Extraction

**Files:**
- Create: `src/pkb/douyin/media.py`
- Test: `tests/test_douyin_media.py`

- [ ] **Step 1: Write path-safety and subprocess tests**

```python
def test_extract_audio_uses_mono_16khz(tmp_path, runner):
    media = TemporaryMedia(tmp_path / "douyin", runner=runner)
    paths = media.prepare("123"); paths.video.write_bytes(b"video")
    media.extract_audio(paths)
    assert runner.calls[-1][-7:] == ["-vn", "-ac", "1", "-ar", "16000", "-y", str(paths.audio)]

def test_cleanup_refuses_path_outside_root(tmp_path):
    with pytest.raises(ValueError, match="temporary root"):
        TemporaryMedia(tmp_path / "root").cleanup(tmp_path / "other")
```

- [ ] **Step 2: Run tests and verify red**

Run: `pytest tests/test_douyin_media.py -q`

Expected: FAIL because `TemporaryMedia` is undefined.

- [ ] **Step 3: Implement dedicated-root lifecycle**

```python
command = [self.ffmpeg, "-i", str(paths.video), "-vn", "-ac", "1",
           "-ar", "16000", "-y", str(paths.audio)]
self.runner(command, check=True, capture_output=True, text=True)
```

Resolve every work directory before deletion, require `work_dir.is_relative_to(root.resolve())`, reject symlinks escaping the root, and classify acquisition failures as unavailable, retryable, or stop-run.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_douyin_media.py -q`

Expected: all tests PASS.

```powershell
git add src/pkb/douyin/media.py tests/test_douyin_media.py
git commit -m "feat: add safe temporary Douyin media lifecycle"
```

### Task 5: SenseVoice Primary and Faster-Whisper Fallback

**Files:**
- Create: `src/pkb/douyin/transcription.py`
- Test: `tests/test_douyin_transcription.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write quality-gate and fallback tests**

```python
def test_primary_valid_transcript_does_not_call_fallback():
    primary=FakeEngine(result("这是完整知识内容", engine="sensevoice")); fallback=FakeEngine()
    got=FallbackTranscriber(primary, fallback).transcribe(Path("a.wav"), voiced_seconds=8)
    assert got.engine == "sensevoice" and fallback.calls == 0

@pytest.mark.parametrize("text", ["", "啊啊啊啊啊啊啊啊", "[UNKNOWN] [UNKNOWN]"])
def test_bad_primary_uses_faster_whisper(text):
    got=FallbackTranscriber(FakeEngine(result(text)), FakeEngine(result("有效内容", engine="faster-whisper"))).transcribe(Path("a.wav"), voiced_seconds=8)
    assert got.engine == "faster-whisper"
```

- [ ] **Step 2: Run tests and verify red**

Run: `pytest tests/test_douyin_transcription.py -q`

Expected: FAIL because transcription module is missing.

- [ ] **Step 3: Implement lazy adapters and conservative gate**

```python
def is_usable(result: TranscriptResult, voiced_seconds: float) -> bool:
    compact = re.sub(r"\s+", "", result.text)
    if not compact: return False
    if voiced_seconds >= 5 and len(compact) < 4: return False
    if "[UNKNOWN]" in result.text and result.text.count("[UNKNOWN]") >= 2: return False
    return max((compact.count(ch) for ch in set(compact)), default=0) / len(compact) < .8
```

Lazy-import FunASR and faster-whisper inside engine constructors so base installs and offline unit tests remain dependency-free. Preserve engine, model, language, and segment timestamps.

- [ ] **Step 4: Add optional dependencies**

```toml
douyin = [
  "funasr>=1.2",
  "faster-whisper>=1.1",
]
```

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_douyin_transcription.py -q`

Expected: all tests PASS.

```powershell
git add pyproject.toml src/pkb/douyin/transcription.py tests/test_douyin_transcription.py
git commit -m "feat: add local Douyin transcription fallback"
```

### Task 6: Durable Pipeline, Audit, and Cleanup Recovery

**Files:**
- Create: `src/pkb/douyin/pipeline.py`
- Test: `tests/test_douyin_pipeline.py`

- [ ] **Step 1: Write end-to-end fake pipeline tests**

```python
def test_pipeline_flushes_record_before_cleanup(tmp_path):
    events=[]; pipeline=trial_pipeline(tmp_path, events)
    report=pipeline.run([item("1")])
    assert events.index("flush") < events.index("cleanup")
    assert report.persisted == report.cleaned == 1

def test_write_failure_keeps_media_and_marks_cleanup_pending(tmp_path):
    pipeline=trial_pipeline(tmp_path, writer=FailingWriter())
    report=pipeline.run([item("1")])
    assert report.failed == 1 and pipeline.media.exists("1")

def test_second_run_does_not_duplicate_jsonl(tmp_path):
    pipeline=trial_pipeline(tmp_path); pipeline.run([item("1")]); pipeline.run([item("1")])
    assert len((tmp_path / "raw.jsonl").read_text(encoding="utf-8").splitlines()) == 1
```

- [ ] **Step 2: Run tests and verify red**

Run: `pytest tests/test_douyin_pipeline.py -q`

Expected: FAIL because `DouyinTrialPipeline` is undefined.

- [ ] **Step 3: Implement orchestration and safe audit codes**

```python
for item in manifest.pending():
    try:
        paths = media.acquire(item); manifest.update(item.work_id, Stage.ACQUIRED)
        media.extract_audio(paths); manifest.update(item.work_id, Stage.AUDIO_READY)
        transcript = transcriber.transcribe(paths.audio, media.voiced_seconds(paths.audio))
        writer.append_once(build_record(item, transcript)); writer.flush_and_sync()
        manifest.update(item.work_id, Stage.PERSISTED); media.cleanup(paths.work_dir)
        manifest.update(item.work_id, Stage.CLEANED)
    except StopRun as exc:
        report.stop_reason = exc.code; break
```

Write an audit JSON with counts for selected, persisted, cleaned, unavailable, failed, cleanup pending, and safe per-item error codes. Never serialize exception messages from remote responses.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_douyin_pipeline.py -q`

Expected: all tests PASS.

```powershell
git add src/pkb/douyin/pipeline.py tests/test_douyin_pipeline.py
git commit -m "feat: orchestrate resumable Douyin transcription trial"
```

### Task 7: Douyin Knowledge Adapter and Search Integration

**Files:**
- Create: `src/pkb/sources/douyin_favorites.py`
- Create: `tests/fixtures/knowledge/douyin-favorites.jsonl`
- Modify: `src/pkb/knowledge/indexer.py`
- Modify: `tests/test_source_adapters.py`
- Modify: `tests/test_knowledge_indexer.py`

- [ ] **Step 1: Write adapter and retrieval tests**

```python
def test_douyin_adapter_builds_transcript_document():
    doc=DouyinFavoritesAdapter().normalize(record(), raw_path=Path("douyin-favorites.jsonl"), raw_line=1)
    assert doc.identity_key == "douyin:work:7"
    assert doc.membership.collection_id == "favorites"
    assert "00:00-00:04 这是口述知识" in doc.plain_content
    assert doc.media_urls == ()

def test_douyin_spoken_phrase_is_searchable(repository, raw_dir):
    _write(raw_dir / "douyin-favorites.jsonl", [record()])
    KnowledgeIndexer(repository).build(raw_dir)
    assert SearchIndex(repository).search("口述知识")[0].source == "douyin"
```

- [ ] **Step 2: Run tests and verify red**

Run: `pytest tests/test_source_adapters.py tests/test_knowledge_indexer.py -q`

Expected: FAIL because the adapter and route do not exist.

- [ ] **Step 3: Implement adapter and route**

```python
if name.startswith("douyin-favorites") and name.endswith(".jsonl"):
    return DouyinFavoritesAdapter()
```

Format content as caption, hashtags, then one timestamped line per segment. Require a stable work ID and canonical HTTPS Douyin URL; ignore all media fields.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_source_adapters.py tests/test_knowledge_indexer.py -q`

Expected: all tests PASS.

```powershell
git add src/pkb/sources/douyin_favorites.py src/pkb/knowledge/indexer.py tests/fixtures/knowledge/douyin-favorites.jsonl tests/test_source_adapters.py tests/test_knowledge_indexer.py
git commit -m "feat: index Douyin transcripts"
```

### Task 8: Bounded CLI and Privacy Defaults

**Files:**
- Modify: `src/pkb/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_repo_privacy.py`

- [ ] **Step 1: Write CLI boundary tests**

```python
def test_douyin_trial_defaults_to_twenty_and_external_temp(monkeypatch, tmp_path):
    captured={}; monkeypatch.setattr("pkb.cli.run_douyin_trial", lambda **kw: captured.update(kw) or 0)
    assert main(["export","douyin-favorites","--output",str(tmp_path/"raw.jsonl"),"--state",str(tmp_path/"state.json")]) == 0
    assert captured["limit"] == 20 and captured["request_delay"] >= 5

def test_douyin_trial_rejects_more_than_twenty():
    assert main(["export","douyin-favorites","--limit","21"]) == 2
```

- [ ] **Step 2: Run tests and verify red**

Run: `pytest tests/test_cli.py tests/test_repo_privacy.py -q`

Expected: FAIL because the command is unsupported.

- [ ] **Step 3: Add command with explicit safe defaults**

```python
douyin = export_subparsers.add_parser("douyin-favorites")
douyin.add_argument("--output", default="data/raw/douyin-favorites.jsonl")
douyin.add_argument("--state", default="data/state/douyin-favorites.state.json")
douyin.add_argument("--report", default="data/state/douyin-favorites.audit.json")
douyin.add_argument("--limit", type=int, choices=range(1, 21), default=20)
douyin.add_argument("--request-delay", type=float, default=7.0)
douyin.add_argument("--temp-root")
```

Resolve the default temp root under `tempfile.gettempdir()`, never under the repository. The CLI prints only counts and safe codes.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_cli.py tests/test_repo_privacy.py -q`

Expected: all tests PASS.

```powershell
git add src/pkb/cli.py tests/test_cli.py tests/test_repo_privacy.py
git commit -m "feat: expose safe Douyin trial CLI"
```

### Task 9: Operations, Offline Acceptance, and Live Smoke Gate

**Files:**
- Modify: `docs/second-brain-operations.md`
- Modify: `tests/test_operations_documentation.py`
- Create: `tests/test_acceptance_douyin.py`

- [ ] **Step 1: Write documentation and offline acceptance tests**

```python
def test_offline_douyin_trial_is_resumable_searchable_and_clean(tmp_path):
    app=acceptance_app(tmp_path, favorites=twenty_fixture_items())
    first=app.run(); second=app.run()
    assert first.selected == 20 and first.cleaned == 20
    assert second.persisted == 0
    assert app.search("第七条口述知识")[0].identity_key == "douyin:work:7"
    assert list(app.temp_root.rglob("*.mp4")) == list(app.temp_root.rglob("*.wav")) == []
```

- [ ] **Step 2: Run acceptance test and verify red**

Run: `pytest tests/test_acceptance_douyin.py -q`

Expected: FAIL until the acceptance harness composes Tasks 1–8.

- [ ] **Step 3: Document exact trial and recovery commands**

```powershell
python -m pip install -e ".[dev,douyin]"
pkb export douyin-favorites --limit 20 --request-delay 7
pkb index build --raw-dir data/raw --db data/knowledge/knowledge.db --strict
pkb search "口述内容中的短语" --db data/knowledge/knowledge.db --source douyin
```

Document login/CAPTCHA stop behavior, resume command, audit interpretation, temporary cleanup guarantees, and the rule that full import requires review of five transcripts and the 20-item audit.

- [ ] **Step 4: Complete acceptance harness and run focused suite**

Run: `pytest tests/test_douyin_models.py tests/test_douyin_manifest.py tests/test_douyin_collector.py tests/test_douyin_media.py tests/test_douyin_transcription.py tests/test_douyin_pipeline.py tests/test_source_adapters.py tests/test_knowledge_indexer.py tests/test_cli.py tests/test_repo_privacy.py tests/test_acceptance_douyin.py tests/test_operations_documentation.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Run full offline suite**

Run: `pytest -q`

Expected: all tests PASS with no network access required.

- [ ] **Step 6: Run one-item opt-in live smoke test**

Run after confirming Chrome is logged into Douyin:

```powershell
pkb export douyin-favorites --limit 1 --request-delay 10 --output "$env:TEMP\douyin-smoke.jsonl" --state "$env:TEMP\douyin-smoke.state.json" --report "$env:TEMP\douyin-smoke.audit.json"
```

Expected: one item is `cleaned`, the JSONL contains transcript text, and the dedicated temporary work directory is empty. If login, CAPTCHA, 403, or 429 occurs, expected behavior is a safe stop with a resumable checkpoint.

- [ ] **Step 7: Commit operations and acceptance**

```powershell
git add docs/second-brain-operations.md tests/test_operations_documentation.py tests/test_acceptance_douyin.py
git commit -m "test: accept Douyin transcript trial"
```

## Full-Import Decision Gate

Do not increase the 20-item limit in this plan. Review `data/state/douyin-favorites.audit.json` and manually compare five speech-bearing transcripts with their source videos. A separate approved plan is required for full-library pagination, long-running rate control, and capacity estimates.
