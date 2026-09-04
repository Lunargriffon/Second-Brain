# Douyin Full Favorites Sync Design

## Goal

Extend the proven 20-item Douyin trial into a resumable full-library sync. One
command discovers the complete authenticated favorites collection, transcribes
every unfinished video, rebuilds the local knowledge index, and refreshes the
Wiki projection.

The existing bounded trial remains available. Existing raw records and manifest
state are preserved and reused.

## Command Contract

`pkb export douyin-favorites --all` selects full-library mode. `--all` and
`--limit` are mutually exclusive. Without `--all`, the existing default of 20
items and the existing `1..20` safety boundary remain unchanged.

Full mode uses the current output, state, report, request-delay, and external
temporary-media defaults. A successful full run also rebuilds the configured
knowledge index and exports the Wiki. These post-processing steps run only after
the media pipeline finishes without a run-stopping condition.

## Discovery

The browser opens the authenticated favorites page and repeatedly scrolls to
the bottom. Each observation extracts canonical numeric `/video/<id>` links,
deduplicates them by work ID, and immediately merges newly discovered items into
the manifest.

Discovery stops after three consecutive observations add no new work IDs. The
collector waits the configured 5–10 second request delay between observations.
The three-observation rule protects against transient lazy-loading pauses while
still giving the run a deterministic end condition.

The browser collector exposes observations incrementally instead of returning a
single in-memory list. This allows manifest checkpoints after every observation
and makes discovery resumable. Existing manifest items count as already known,
but a resumed run still scans to the real end so that newer or previously unseen
favorites are found.

## Processing and Recovery

After discovery, the existing `DouyinPipeline` processes every non-terminal
manifest item. Items already at `cleaned` are skipped. Items interrupted at
`media_acquired`, `audio_ready`, `transcribed`, or `persisted` resume from that
stage. Failed items are retried on the next run; unavailable items remain
terminal unless the user explicitly resets their state in a future feature.

Media acquisition retains the existing order:

1. yt-dlp with the existing Chrome session;
2. OpenCLI browser fallback;
3. a per-work browser session, bounded network-capture polling, HTTPS media URL
   validation, and unconditional session release.

A failure for one work is recorded with a safe code and does not stop later
works. Authentication loss, CAPTCHA, HTTP 403, or HTTP 429 remains a run-stopping
condition. Raw JSONL writes remain append-once by work ID, and temporary media is
removed only after durable persistence.

## Index and Wiki Refresh

When discovery and media processing finish without a run-stopping condition,
full mode runs the same strict index build and Wiki export commands used by the
operations workflow. Index or Wiki failures produce a non-zero command result
and a safe aggregate error; they never roll back or delete synchronized raw
records.

The final audit contains aggregate counts for discovered, selected, persisted,
cleaned, unavailable, failed, and cleanup-pending items, plus whether discovery
reached the end and whether index and Wiki refreshes succeeded. It contains no
cookies, media URLs, transcripts, or browser payloads.

## Safety and Compatibility

- The original bounded trial remains the default and keeps its 20-item limit.
- Full mode requires explicit `--all`.
- Existing state and raw data are never deleted or rewritten.
- Request delay remains constrained to 5–10 seconds.
- Browser and download errors are sanitized before entering reports.
- Personal raw data, generated index files, and Wiki output remain ignored by
  Git under the existing repository policy.

## Testing

Automated tests cover:

- `--all` and `--limit` mutual exclusion and bounded-mode compatibility;
- full discovery across multiple scroll observations;
- three consecutive no-growth observations as the end condition;
- manifest checkpointing after each observation;
- deduplication against existing manifest state;
- resumption from intermediate pipeline stages;
- per-item failure continuation and run-stopping failures;
- post-sync index and Wiki orchestration;
- safe aggregate audit output.

A live acceptance run uses the current authenticated browser session and the
existing collection. Completion requires all discoverable works to be present in
the manifest, all processable works to reach `cleaned`, no cleanup pending, a
strict index build with zero failures, and a Wiki export with zero conflicts.
