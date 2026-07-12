# Personal Knowledge Base (PKB)

## Overview

Personal Knowledge Base (PKB) is an AI-powered knowledge management system designed to transform scattered information into structured, searchable, and continuously evolving knowledge.

The initial data source is Zhihu collections, while the long-term vision is to support multiple knowledge sources, including GitHub Stars, PDFs, ArXiv papers, web articles, videos, RSS feeds, and personal notes.

This project is **not** intended to archive articles. Its purpose is to build a long-term AI-native knowledge system.

---

# Objectives

The project aims to:

- Export knowledge from multiple platforms.
- Normalize all data into a unified schema.
- Deduplicate similar content.
- Generate AI summaries.
- Classify information into topics.
- Build semantic relationships between knowledge.
- Recommend high-value content.
- Create a searchable and maintainable personal knowledge base.

The ultimate goal is to spend more time learning and thinking, and less time organizing information.

---

# Project Workflow

```text
Data Sources
    │
    ├── Zhihu Collections
    ├── GitHub Stars
    ├── PDF Documents
    ├── ArXiv
    ├── Web Articles
    ├── Videos
    └── Personal Notes
            │
            ▼
      Data Export
            │
            ▼
   Unified JSON Schema
            │
            ▼
      AI Processing
            ├── Deduplication
            ├── Classification
            ├── Tag Generation
            ├── Summarization
            ├── Topic Clustering
            ├── Knowledge Linking
            └── Priority Scoring
            │
            ▼
    Personal Knowledge Base
```

---

# Roadmap

## Phase 1 — Data Acquisition

Build a stable Zhihu collection exporter.

Phase 1 prioritizes reliability over completeness.

### Success Criteria

- Exported item count matches the number shown in the Zhihu collection.
- Every exported record must conform to the current schema defined in `schema/article.v1.json`.
- Article content is successfully extracted whenever accessible to the logged-in user.
- Image downloading is disabled by default and may be enabled after text export is stable.
- The exporter supports resumable execution.

---

## Phase 2 — AI Knowledge Processing

Build an AI processing pipeline that can:

- Generate summaries
- Generate tags
- Detect duplicates
- Cluster related articles
- Score reading priority
- Build semantic relationships

AI-generated results must be stored separately from raw source data.

---

## Phase 3 — Multi-source Integration

Support additional knowledge sources, including:

- GitHub
- PDFs
- ArXiv
- Obsidian
- RSS
- YouTube
- Bilibili
- Web clipping

---

# Data Schema

All imported content should be converted into a unified JSON schema.

Every record must conform to `schema/article.v1.json`.

When the schema changes, increment `schema_version`.

Previously exported data should remain unchanged and should **not** be rewritten automatically.

---

# Phase 1 CLI

Phase 1 exposes one command:

```bash
pkb export zhihu \
  --collection-url <URL> \
  --output data/raw/zhihu.jsonl
```

The default checkpoint path is:

```text
data/state/zhihu.state.json
```

Tests use a fake Zhihu client and fixtures, so they run offline without a cookie, network access, or a logged-in browser session.

The real Zhihu network adapter is intentionally conservative:

- It requires `ZHIHU_COOKIE` from `.env` or `--cookie`.
- It defaults to `--limit 5` for first-run safety.
- It runs single-threaded with `--request-delay 2` by default.
- It stops on missing cookie, unsupported content types, non-JSON responses, and HTTP 401/403/429.

For a first real-account test:

```bash
pkb export zhihu \
  --collection-url <URL> \
  --output data/raw/zhihu.jsonl \
  --state data/state/zhihu.state.json \
  --limit 5
```

---

# Notes

This project is intended **only for personal learning and knowledge management**.

Users should comply with applicable platform terms of service and applicable laws when exporting their own data.

---

# Long-term Vision

The final product should become a continuously evolving Personal Knowledge Base rather than a static archive.

Every new piece of information should automatically enter the system, be understood by AI, linked with existing knowledge, and become part of an expanding knowledge network.
