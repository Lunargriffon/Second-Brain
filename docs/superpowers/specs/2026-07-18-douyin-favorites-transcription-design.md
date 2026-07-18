# Douyin Favorites Transcription Design

## Goal

Turn a small sample of the user's Douyin favorites into searchable Second-Brain documents without retaining video or audio files. Phase 1 processes the 20 most recent favorites and proves acquisition, local transcription, resumability, deletion, and knowledge-index integration before any full-library import.

## Scope

Phase 1 includes:

- Reading the 20 most recent favorites from the user's logged-in Douyin web session.
- Capturing stable source metadata and a canonical work URL.
- Temporarily acquiring one work at a time and extracting audio.
- Transcribing primarily with SenseVoice-Small on CPU.
- Retrying failed or low-quality transcripts with faster-whisper on CPU INT8.
- Persisting raw JSONL records and indexing them through the existing knowledge pipeline.
- Deleting temporary video and audio after a durable successful record is written.
- Producing an audit report that accounts for all 20 selected works.

Phase 1 excludes OCR, cover or media archiving, speaker diarization, AI summaries, automatic tagging beyond existing derivation behavior, comments, likes, posting, and full-library import.

## Constraints

- All media processing occurs locally.
- No permanent video or audio archive is created.
- The collector is read-only and uses the user's existing authenticated browser session.
- Requests are single-threaded with a configurable 5–10 second delay.
- HTTP 403/429 responses, login challenges, or CAPTCHA detection stop the run rather than attempting bypass.
- The MX450 GPU has only 2 GB VRAM, so Phase 1 uses CPU inference for predictable operation.
- Private credentials and browser cookies must never enter JSONL, logs, reports, or git-tracked files.

## Architecture

```text
Logged-in Douyin favorites page
             |
             v
      Favorites collector
             |
             v
       resumable manifest
             |
             v
  one-work temporary media area
             |
             v
       FFmpeg audio extract
             |
             v
 SenseVoice -> quality gate -> faster-whisper fallback
             |
             v
   data/raw/douyin-favorites.jsonl
             |
             v
       Douyin source adapter
             |
             v
 Existing repository, FTS, derivations, and wiki projections
```

The collector, media fetcher, transcriber, raw writer, and source adapter remain separate components. Each can be tested with fixtures without accessing Douyin.

## Acquisition

The collector reads the visible authenticated favorites list and selects the first 20 unique works in the site's current order. Each manifest entry contains:

- Douyin work ID.
- Canonical work URL.
- Author ID and display name when visible.
- Caption/title and hashtags when visible.
- Source publication time when visible.
- Favorite observation time.
- Acquisition status and last error code.

The work ID is the idempotency key. Re-running the same sample must not duplicate records. Pagination and scrolling stop as soon as 20 unique work IDs are collected.

The browser-facing collector is treated as replaceable because Douyin markup and internal endpoints may change. Downstream stages consume only the manifest contract.

## Transcription

Only one work may occupy the temporary media area at a time.

1. Acquire the media into a directory outside the repository.
2. Extract mono 16 kHz audio with FFmpeg.
3. Run SenseVoice-Small with Chinese as the preferred language.
4. Normalize punctuation and retain segment timestamps.
5. Apply a conservative quality gate.
6. If the primary result is empty, abnormally short relative to voiced duration, or contains an excessive unknown/repetition pattern, retry with faster-whisper `small` on CPU INT8.
7. Choose the valid transcript; preserve which engine and model produced it.

The quality gate detects obvious pipeline failures, not semantic truth. Phase 1 includes a manual review of five transcripts against their source videos to estimate practical accuracy.

## Raw Record Contract

Each line in `data/raw/douyin-favorites.jsonl` is one self-contained source record:

- `id`, `url`, `author_id`, `author`, `caption`, `hashtags`.
- `published_at`, `observed_at`.
- `transcript_text`.
- `transcript_segments` with start/end timestamps and text.
- `transcription_engine`, `transcription_model`, `language`.
- `source_duration_seconds` when available.
- `content_fingerprint` derived from stable metadata and transcript.
- `status` and non-sensitive diagnostic fields.

The raw record never contains local temporary paths, cookies, request headers, or media bytes.

## Knowledge Integration

A `DouyinFavoritesAdapter` maps `douyin-favorites*.jsonl` to the existing `NormalizedDocument` model:

- Identity: `douyin:work:<work_id>`.
- Canonical URL: the stable work URL.
- Title: caption truncated only for title presentation; the full caption remains in content.
- Author: Douyin display name.
- Plain content: caption, hashtags, and timestamped transcript in a readable text layout.
- Media URLs: empty, because Phase 1 intentionally does not archive media.
- Source membership: source `douyin`, item ID equal to the work ID, collection `favorites`.

Indexing then reuses the existing repository, full-text search, derivation queue, and wiki projection. A query matching a phrase spoken in a processed video must return that Douyin document.

## State, Cleanup, and Recovery

The manifest/checkpoint records stage transitions: `discovered`, `acquired`, `audio_ready`, `transcribed`, `persisted`, `indexed`, `cleaned`, or `failed`.

Cleanup occurs only after the JSONL record is flushed durably. If indexing fails, the raw record remains and can be indexed again without reacquiring media. If transcription fails, temporary media may remain until the run ends for diagnosis, after which a cleanup pass deletes it unless an explicit debug-retention flag was selected. The default is no retention.

Startup recovery removes abandoned temporary files only after confirming they are under the dedicated temporary root and are not referenced by an active checkpoint.

## Error Handling and Safety

- Login expired or challenge detected: stop the whole collector and report `auth_required`.
- 403/429: stop immediately and preserve the checkpoint.
- Deleted/unavailable work: record `unavailable` and continue after the normal delay.
- Media acquisition failure: retry with bounded exponential backoff, then record failure.
- No speech: persist metadata with `no_speech` only when the recognizer and duration checks agree.
- Transcription failure: retain a diagnostic code without exception traces containing paths or secrets.
- JSONL write failure: do not delete temporary media until durable persistence succeeds.
- Cleanup failure: mark `cleanup_pending`; a later cleanup command retries only within the verified temporary root.

## Test Strategy

All automated tests use local fixtures and fake browser/media/transcription clients.

- Collector tests: order, 20-item limit, deduplication, pagination stop, auth/challenge detection, and rate-limit stop.
- Manifest tests: legal transitions, checkpoint resume, and deterministic serialization.
- Transcription tests: primary success, quality-gate fallback, no-speech handling, timestamp preservation, and model provenance.
- Cleanup tests: deletion after durable write, retention before write, and refusal to delete outside the temporary root.
- Adapter tests: identity, canonical URL, transcript content, empty media list, and invalid record diagnostics.
- Integration tests: JSONL ingestion, idempotent re-index, full-text retrieval by spoken phrase, and no media rows.

One opt-in live smoke test may read a single favorite using the local authenticated session. It is excluded from the offline test suite.

## Phase 1 Acceptance

The trial passes only when:

- Exactly 20 unique favorites are accounted for as indexed, unavailable, or failed with explicit reasons.
- At least one real favorite completes the entire live path.
- Five manually reviewed speech-bearing videos have usable transcripts; discrepancies are recorded before full import is approved.
- Re-running creates no duplicate documents or JSONL records.
- Searching a spoken phrase returns the expected Douyin document.
- No cookies or tokens appear in tracked files or logs.
- No video or audio remains after a successful run, and any cleanup-pending files are explicitly reported.
- 403, 429, CAPTCHA, and expired-login simulations stop safely and resume from the checkpoint.

Full-library import is a separate phase and is not authorized until this acceptance report is reviewed.
