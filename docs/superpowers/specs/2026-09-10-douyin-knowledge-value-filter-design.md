# Douyin Knowledge-Value Filter Design

## Goal

Turn Douyin ingestion into a knowledge-library workflow rather than a complete
media archive. The sync must discover every favorited video, judge each video
from its own metadata, and download, transcribe, and index only videos likely to
carry reusable knowledge. Folder names are deliberately ignored because videos
may be filed inconsistently. Beauty, scenery, entertainment, and other
appreciation-only videos remain on Douyin and are not retained locally.

## Scope and Decisions

- Discovery continues to page the authenticated flat favorites feed to its
  server-reported end and deduplicates by work ID. Named favorite folders are
  not an input to eligibility decisions.
- Classification uses metadata available before media acquisition: caption or
  title, hashtags, and author display name. It never downloads media merely to
  decide whether the media is worth downloading.
- The local corpus is rebuilt conservatively. Existing files are backed up;
  accepted records are copied into a fresh corpus, and excluded records are
  omitted. The old corpus is not destructively edited in place.
- The first production policy is a deterministic, versioned scorer. This keeps
  decisions reproducible, private, fast, and testable. A future model-backed
  classifier may implement the same interface, but is outside this change.
- Ambiguous metadata is excluded by default. False positives pollute a
  knowledge base and incur acquisition cost, while excluded favorites remain
  available on Douyin.

## Architecture

### Metadata and state

`FavoriteItem` gains classification state: `eligibility`,
`eligibility_reasons`, `classifier_version`, and
`classification_input_hash`. Manifest schema version 2 migrates version-1
items losslessly. Media stages remain separate from classification state so a
classification decision never masquerades as completed media processing.

The raw knowledge record keeps its source identity `douyin:work:<work_id>`.
Folder membership is not stored as ranking evidence and does not influence the
decision.

### Classifier boundary

A focused `pkb.douyin.eligibility` module owns:

- `VideoMetadata`, the minimal normalized classifier input;
- `EligibilityDecision` with `keep|exclude`, stable reason codes, policy
  version, and canonical input hash;
- `KnowledgeValueClassifier`, a protocol;
- `RuleBasedKnowledgeValueClassifier`, the version-1 production policy.

The policy normalizes Unicode, whitespace, and case before scoring. Positive
signals cover explanations, tutorials, methods, reviews, analysis, learning,
work, business, finance, technology, health, law, history, science, and other
reusable subject matter. Negative signals cover beauty display, scenery,
wallpaper, dance, lip-sync, fan edits, gaming highlights, mood clips, and other
appreciation-only material. Strong negative signals exclude unless stronger,
specific knowledge signals are also present. Generic engagement phrases and
author names never suffice to keep a video.

Every decision records reason codes and the canonical metadata hash. Changing
rules changes the classifier version, which invalidates old decisions and makes
the item eligible for reclassification without redownloading excluded media.

### Pipeline data flow

The full sync becomes:

1. Discover and checkpoint all unique favorite metadata.
2. Classify every item whose classifier version or input hash is stale.
3. Mark excluded items terminal for the current policy without acquiring media.
4. Send only kept items into the existing resumable media/transcription
   pipeline.
5. Append only successful kept transcripts to the fresh raw corpus.
6. Build the search index and Wiki only after discovery and processing finish.

An existing cleaned item is not automatically trusted during a rebuild. Its
metadata is reclassified. If kept, its raw record is copied without downloading
or retranscribing. If excluded, it is absent from the fresh corpus.

### Persistence and audit

Classification decisions live in the manifest, not in the knowledge JSONL.
Excluded videos retain only operational discovery metadata and the decision in
the ignored manifest so later metadata or policy changes can be detected. Their
title, author, caption, media, and transcript are not copied into the knowledge
corpus, search index, or Wiki.

The aggregate audit adds `eligible`, `excluded`, `classified`, and reason-code
counts. It must never contain captions, author names, hashtags, transcripts, or
media URLs. Completion means every discovered item is classified and every kept
item is either cleaned or explicitly unavailable; retryable failures keep the
run incomplete. Index and Wiki refresh flags may be true only for a complete
run.

## Safe Corpus Rebuild

The current unfiltered raw JSONL and manifest are copied to a timestamped backup
directory under `data/backups/`. Rebuild uses new temporary raw, manifest, and
audit paths. It classifies all 824 currently known items, reuses accepted raw
records, and processes accepted items that lack transcripts. Only after
validation succeeds are the canonical files replaced atomically. A failed or
interrupted rebuild leaves the canonical corpus and its backup intact.

Validation requires:

- raw work IDs are unique;
- every raw transcript is non-empty;
- every raw work ID has a current `keep` decision;
- no `exclude` work ID appears in raw data, the search index, or Wiki output;
- every discovered item has a current decision;
- no kept retryable item remains unfinished;
- temporary media cleanup count is zero.

## Error Handling

- Authentication, CAPTCHA, HTTP 403, and HTTP 429 stop the run and preserve all
  checkpoints.
- Classifier programming or state-integrity errors stop the run; they do not
  silently exclude content.
- Media unavailable errors remain terminal for an eligible item and are counted
  separately from policy exclusions.
- Retryable media and transcription failures leave the run incomplete and do
  not trigger index or Wiki publication.
- Atomic file replacement and append-once work IDs preserve crash recovery.

## CLI and Operations

`pkb export douyin-favorites --all` uses the knowledge-value policy by default.
The CLI prints aggregate counts only. A `--reclassify` option forces current
metadata through the active policy; the safe rebuild command uses it. There is
no option in this workflow to archive all videos, because that conflicts with
the repository's knowledge-management purpose.

The operations guide documents policy versioning, exclusion semantics, backup
location, resume behavior, aggregate validation, and the exact rebuild command.

## Testing

Tests cover normalization, positive and negative signals, ambiguous fail-closed
behavior, conflicting signals, stable hashes, policy-version invalidation,
manifest v1-to-v2 migration, pre-acquisition filtering, raw-record reuse,
interruption recovery, aggregate privacy, atomic promotion, and end-to-end
absence of excluded IDs from raw data, index, and Wiki. A live dry discovery
must report classification counts before the long rebuild begins.
