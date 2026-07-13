# Personal Knowledge Base (PKB)

PKB turns personal exports into a local, searchable, and reviewable knowledge
base. Raw exports remain evidence; SQLite, AI derivations, and Obsidian pages are
replaceable projections. Human notes, tags, priorities, and reading decisions
remain separate from AI output.

The repository is safe to open-source. Personal exports, cookies, API keys,
databases, generated Vault content, and acceptance reports are ignored and must
stay private.

## Architecture

```text
Private evidence                       Rebuildable local projections

data/raw + data/images + data/frozen
                 |
                 v
        adapters + normalization ---> SQLite index + Chinese FTS
                                         |          |
                                         |          +--> bounded search/review
                                         v
                                  versioned AI output
                                         |
                                         v
                                  generated Obsidian Vault
                                         ^
                                         |
                             vault/user + human state
```

The detailed, approved boundaries are in the
[architecture design](docs/superpowers/specs/2026-07-11-second-brain-architecture-design.md).
Exact daily, backup, privacy, and recovery commands are in the
[operations guide](docs/second-brain-operations.md).

## Install

Python 3.11 or later is required.

```powershell
python -m pip install -e ".[dev,search]"
```

Install the optional local MCP adapter with:

```powershell
python -m pip install -e ".[dev,search,mcp]"
```

## Recommended Daily Workflow

```text
index build -> search/derive small batch -> wiki export -> review today
```

```powershell
pkb index build --raw-dir data/raw --db data/index/knowledge.db --strict
pkb search "知识管理" --db data/index/knowledge.db --limit 10
pkb derive articles --db data/index/knowledge.db --limit 5 --dry-run
pkb derive articles --db data/index/knowledge.db --limit 5
pkb wiki export --db data/index/knowledge.db --vault vault
pkb wiki check --vault vault --format json
pkb review today --db data/index/knowledge.db --count 5 --vault vault
```

Always preview AI scope with `--dry-run` and a finite `--limit`. Provider calls
may send private article text outside the machine; configure one only after
reviewing its privacy and retention policy.

## Current Capabilities

- Resumable, bounded Zhihu collection and author export.
- Immutable raw JSONL ingestion with cross-source identity and URL aliases.
- Rebuildable SQLite schema and deterministic incremental indexing.
- Chinese-capable trigram lexical search with recorded real-corpus benchmarks.
- Versioned, source-grounded article derivations with leases, heartbeat,
  retries, audit logs, and dead-letter handling.
- Bounded relationship candidates and strict structured relation results.
- Safe deterministic Obsidian export, manifest validation, attachment policy,
  and protected human notes.
- Reading state, manual tags, deterministic daily review, and CLI workflows.
- Evidence-gated semantic retrieval evaluation; the current decision is to keep
  lexical search because only fake embedding evidence is available.
- A local stdio MCP adapter for the stable knowledge operations.

No custom web application is authorized. The current evidence report records
`decision: defer` until actual usage demonstrates a need and a remote-access
security model and maintenance budget are approved.

## Evidence and Privacy Boundaries

- `data/raw`, `data/images`, and `data/frozen` are private evidence and are not
  rewritten by index, derivation, Wiki, review, or acceptance workflows.
- `data/index`, `data/derived`, generated Vault pages, manifests, and reports are
  private rebuildable projections.
- `vault/user` and human state in SQLite are private, authoritative, and must be
  backed up before a rebuild.
- `.env`, cookies, provider credentials, personal query sets, titles, IDs, and
  real acceptance results must never be committed.

Generated files being ignored by Git does not make a running service secure.
Keep MCP on local stdio and do not publish a database, Vault, or remote listener.

## Zhihu Acquisition

The real network adapter is intentionally conservative. It requires
`ZHIHU_COOKIE` from an untracked `.env` or `--cookie`, defaults to five items,
runs single-threaded with a two-second request delay, and stops on authentication,
rate-limit, non-JSON, and unsupported-content errors.

```powershell
pkb export zhihu `
  --collection-url <URL> `
  --output data/raw/zhihu.jsonl `
  --state data/state/zhihu.state.json `
  --limit 5
```

Use the exporter only for data you are permitted to access and comply with
applicable platform terms and laws.

## Test

All committed tests are offline and use public fixtures or deterministic fake
providers:

```powershell
python -m pytest -q
```

Real-corpus reports are generated under ignored `data/state` paths so private
titles, document IDs, and queries do not enter the public repository.
