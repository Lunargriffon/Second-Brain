# Second Brain Operations and Recovery

This guide describes the commands shipped by the repository. Run them from the
repository root after installing the project:

```powershell
python -m pip install -e ".[dev,search,mcp]"
```

Use a dedicated database such as `data/index/knowledge.db`. The evidence roots
`data/raw`, `data/images`, and `data/frozen` are inputs: index, derivation, Wiki,
review, and acceptance commands must not rewrite them. SQLite databases,
`data/derived`, generated Vault pages, and generated reports are rebuildable
projections. `vault/user` and the SQLite `reading_state`/manual-label rows are
human state and require backups.

## Build the Index

Start with a finite, strict build and keep its machine-readable report:

```powershell
pkb index build --raw-dir data/raw --db data/index/knowledge.db --strict --report data/state/index-build.json
pkb index status --db data/index/knowledge.db --format json
```

The first command indexes supported JSONL files, updates Chinese lexical search,
and queues derivation jobs when source content changed. A second identical build
should report zero creates, updates, search updates, and new jobs. Strict mode
fails the transaction on a malformed supported record; without `--strict`, bad
records are reported and valid records continue.

## Import Douyin Favorites Transcripts

This is a local-only, bounded trial. It reuses the authenticated Chrome session;
do not export, paste, or upload cookies. Install the speech and search extras plus
the local executables `ffmpeg`, `ffprobe`, `yt-dlp`, and OpenCLI:

```powershell
python -m pip install -e ".[dev,search,douyin]"
pkb export douyin-favorites --limit 20 --request-delay 7
```

On Windows, keep Chrome open with the OpenCLI extension connected. When Chrome
cookie copying is blocked by DPAPI or an in-use cookie database, the downloader
uses the authenticated browser tab and its captured Douyin detail response. It
does not export or persist browser cookies.

The command never accepts more than 20 items and never accepts a request delay
below five seconds. Video and WAV files live under the operating-system temporary
directory and are deleted only after the transcript JSONL has been flushed to
durable storage. The audit is written atomically to
`data/state/douyin-favorites.audit.json`; `cleanup_pending=0` confirms that no
persisted item still has temporary media waiting for deletion.

Speech recognition processes long recordings in 30-second chunks. Each successful
chunk is checkpointed atomically beside the temporary WAV, so an interrupted run
resumes after the completed chunks instead of retranscribing the recording from
the beginning. Keep the temporary media root intact when recovering; rerun the
identical command and the checkpoint is accepted only when the audio file, model,
and chunk settings still match.

A missing captured playback URL is reported as `media_url_unavailable`. The first
occurrence remains retryable. If the same item produces that same code in
two consecutive runs, its manifest entry becomes `unavailable` with the code and count
retained as evidence. Browser, authentication, rate-limit, and transcription
failures never use this terminal rule: they stay visible and retryable or stop the
run according to their safe error code.

If the command reports `auth_required`, log into Douyin in Chrome and run the
same command again. `captcha`, `http_403`, and `http_429` are safe stop signals:
complete any visible challenge yourself, wait before retrying, and do not try to
bypass platform controls. The manifest checkpoint makes the identical command
the recovery command; completed items are not downloaded or transcribed twice.

Index and search the retained text locally:

```powershell
pkb index build --raw-dir data/raw --db data/index/knowledge.db --strict
pkb search "口述内容中的短语" --db data/index/knowledge.db --source douyin
```

For the bounded trial, manually compare five speech-bearing transcripts with
their source videos before relying on transcription quality.

Full mode is a knowledge-value sync, not a media archive. It classifies each
video independently from its caption/title, hashtags, and author metadata;
folder names are not used because favorites may be filed inconsistently.
Tutorials, methods, analysis, reusable experience, and subject knowledge are
kept. Beauty display, scenery, wallpaper, entertainment clips, gaming
highlights, travel guides, and similar appreciation-only material are excluded
before download; ambiguous metadata is excluded by default.

To apply the current policy to every known favorite and resume the filtered
full sync, run:

```powershell
pkb export douyin-favorites --all --reclassify --request-delay 7
```

Full mode pages the authenticated flat favorites feed and considers discovery
complete only after three consecutive observations add no new work. It
checkpoints each batch and every versioned classification decision. When the
policy excludes records that already exist locally, it validates the complete
JSONL, copies the old corpus to `data/backups/douyin-favorites`, and atomically
replaces the canonical file with kept records only. Rerunning the identical
command resumes unfinished kept videos without downloading excluded videos.

The command returns nonzero and does not refresh the index or Wiki while any
kept item has a retryable failure or pending cleanup. After a complete run, the
audit reports aggregate `classified`, `eligible`, `excluded`, and reason counts;
then the strict index and generated Wiki are refreshed.

## Search

Search is local and lexical by default:

```powershell
pkb search "知识管理" --db data/index/knowledge.db --limit 10 --format json
pkb search "检索" --db data/index/knowledge.db --source zhihu --collection 123 --limit 10
```

When the saved `ZHIHU_COOKIE` is expired but Chrome is already authenticated,
use the explicit browser-session path. The existing Cookie path remains the
default:

```powershell
pkb export zhihu-batch `
  --collections-file data/config/zhihu-collections.txt `
  --output-dir data/raw `
  --state-dir data/state `
  --limit 0 `
  --browser-session
```

Chrome and the OpenCLI extension must remain open for the duration of this
command. OpenCLI is used read-only; cookies are never written to project files.

Limits must be positive. Search results are projections; rebuild them from the
database and evidence instead of editing them.

## Derive a Small Batch

Provider settings belong in the untracked `.env` file. Never commit API keys,
cookies, prompts containing private full text, provider responses, or personal
run reports. A configured OpenAI-compatible provider receives the selected
article text, so review its retention and training policy before use.

Preview cost and scope before every call:

```powershell
pkb derive status --db data/index/knowledge.db --format json
pkb derive articles --db data/index/knowledge.db --limit 5 --dry-run
pkb derive articles --db data/index/knowledge.db --limit 5
pkb derive relations --db data/index/knowledge.db --document-limit 50 --per-document-limit 3 --pair-limit 30 --dry-run
```

Remove `--dry-run` from the relation command only after inspecting its pair
count. Use `--unlimited` only after an explicit full-corpus decision; it is not
the safe daily default. Derivations are versioned and source-grounded and do not
replace manual tags or reading decisions.

## Knowledge Layer Status

The Fact/Entity knowledge-layer schema and transactional repositories exist,
and their seven lifecycle acceptance scenarios can be verified with:

```powershell
python -m pytest tests/test_fact_entity_acceptance.py -v
```

Fact extraction and `pkb think` are not enabled yet. Do not populate the
knowledge tables manually; doing so bypasses evidence-span validation,
lifecycle transitions, invalidation propagation, and audit events.

### Cross-domain Job Scope Status

Schema v8 and the scoped `JobQueue` are enabled, while legacy document-anchored
article jobs remain supported. Valid scope types are `document`, `entity`,
`fact`, `fact_relation`, and `synthesis`; every job must have exactly one
`anchor` scope. Rows in `job_scopes` are infrastructure and audit data and must
not be edited manually. Fact extraction, knowledge maintenance workers,
synthesis, and the dream cycle remain disabled until their separately reviewed
plans are implemented.

## Export the Vault

Render the deterministic projection and then check it:

```powershell
pkb wiki export --db data/index/knowledge.db --vault vault --report data/state/wiki-export.json
pkb wiki check --vault vault --format json
```

Use `--copy-attachments` only when indexed media has a valid local path. The
exporter owns generated pages and its manifest, but not `vault/user`. It refuses
to silently overwrite locally modified generated pages. Inspect stale paths
before removal:

```powershell
pkb wiki clean --vault vault --stale-only --dry-run
```

Run the same command without `--dry-run` only after reviewing the list.

## Daily Review

Generate or print a bounded daily queue, then record an explicit decision:

```powershell
pkb review today --db data/index/knowledge.db --count 5 --date 2026-07-13 --vault vault
pkb review mark DOCUMENT_ID --db data/index/knowledge.db --status read --date 2026-07-13
```

Allowed statuses are `read`, `queued`, and `ignored`. Omitting `--date` uses the
current local date. Daily selection and generated Markdown are deterministic for
the same database and date.

## Recover Expired Jobs

Article workers use a 15-minute lease and heartbeat it during work. If a worker
crashes, an expired `running` job is atomically reclaimed by the next bounded
article run; there is no separate force-unlock command:

```powershell
pkb derive status --db data/index/knowledge.db --format json
pkb derive articles --db data/index/knowledge.db --limit 5 --dry-run
pkb derive articles --db data/index/knowledge.db --limit 5
```

Wait until the lease has actually expired. Do not edit lease columns manually.
Repeatedly exhausted jobs move to `dead-letter` and are not silently revived.
For ordinary `failed` jobs, inspect status and reset only a finite batch:

```powershell
pkb derive retry --db data/index/knowledge.db --status failed --limit 5
```

`derive retry` changes failed jobs to pending; it does not call the provider and
does not touch succeeded or dead-letter jobs.

## Rebuild Projections

Back up human state first. Rebuild search in place with:

```powershell
pkb index rebuild --raw-dir data/raw --db data/index/knowledge.db --report data/state/index-rebuild.json
pkb wiki export --db data/index/knowledge.db --vault vault --report data/state/wiki-rebuild.json
pkb wiki check --vault vault --format json
```

For a clean reproducibility check, build a new database path and temporary
Vault, compare reports/logical hashes, then swap projections only after review.
Never delete `data/raw`, `data/frozen`, `vault/user`, or the only database copy
that contains human state.

A normalization implementation change is deliberately not allowed to enqueue
the whole corpus silently. Preview and explicitly queue a finite migration:

```powershell
pkb derive migrate --db data/index/knowledge.db --normalization-version 2 --limit 20 --dry-run
pkb derive migrate --db data/index/knowledge.db --normalization-version 2 --limit 20
```

## Back Up Human State

Stop PKB writers before copying SQLite. Back up the database together with user
notes; keeping only generated Markdown loses `reading_state`, priorities, and
manual labels:

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Force "backups/$stamp" | Out-Null
Copy-Item data/index/knowledge.db "backups/$stamp/knowledge.db"
Copy-Item vault/user "backups/$stamp/user" -Recurse
```

Store backups outside the public repository and test restoration periodically.
Generated `vault/articles`, `vault/_index`, and daily pages can be recreated;
human files must be restored before exporting into a replacement Vault.

## Start MCP Locally

Install the `mcp` extra and configure the MCP client to launch:

```powershell
python -m pkb.interfaces.mcp --db data/index/knowledge.db
```

The shipped adapter composes the existing repository services, registers
exactly five tools, and calls `FastMCP.run(transport="stdio")`; it does not bind
an HTTP interface and rejects remote-listener options such as `--host`. Keep the
process local and let the MCP client launch it over stdio. Do not wrap it in a
remotely reachable HTTP server without a separate approved authentication,
authorization, TLS, and threat model.
