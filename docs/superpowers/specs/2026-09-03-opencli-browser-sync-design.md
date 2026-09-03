# OpenCLI Browser-Session Sync Design

## Goal

Synchronize the user's Zhihu and Douyin favorites from the authenticated Chrome
session without exporting or persisting browser cookies. Preserve the existing
raw JSONL contracts, resumable checkpoints, bounded Douyin trial, and temporary
media cleanup guarantees.

## Architecture

OpenCLI is an authentication boundary, not a storage layer. A small browser
gateway invokes stable, read-only OpenCLI commands and converts their structured
JSON into the existing source records. The repositories, indexer, and Vault
exporter remain unchanged.

For Zhihu, the gateway pages through `zhihu collection` and resolves full item
content with the matching read-only detail commands. The existing HTTP/Cookie
client remains available as a compatibility path.

For Douyin, the downloader first uses the current yt-dlp path. If Chrome cookie
copy or DPAPI decryption fails, it opens the saved work URL in an OpenCLI browser
session. Direct HTTPS video sources are downloaded immediately. MediaSource
`blob:` pages are handled by capturing the page's `aweme/detail` response and
selecting an HTTPS play URL from the response body. No signed request URL,
response body, or cookie is logged or persisted.

## Boundaries and Safety

- All OpenCLI operations are read-only.
- Douyin remains limited to 20 items per run.
- Only HTTPS media URLs from the current video detail response are accepted.
- Raw records contain transcript text and source metadata, never media URLs,
  cookies, local paths, or full remote error bodies.
- Media is written only below the dedicated temporary root and deleted after a
  durable JSONL flush.
- Login, CAPTCHA, 403, 429, missing response, malformed response, and timeout
  conditions produce stable safe codes and resumable state.

## Testing

Tests use fake OpenCLI processes and captured minimal response shapes. They
cover pagination, deduplication, detail normalization, direct HTTPS media,
`blob:` response capture, malformed/absent media URLs, subprocess encoding,
checkpoint resume, and cleanup. Focused tests and the full offline suite must
pass before live retry.

## Acceptance

- Zhihu sync succeeds while `.env` contains an expired Cookie, provided OpenCLI
  reports the browser session logged in.
- The remaining Douyin trial items can be persisted or receive an explicit safe
  unavailable code; no item remains cleanup-pending.
- Rebuilding the knowledge index and Vault completes without errors.
