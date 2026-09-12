# AGENTS.md

## Project Context

This repository implements an AI-assisted Personal Knowledge Base (PKB).

Project goals, roadmap and design philosophy are documented in **README.md**.

This document defines **how AI agents should work inside this repository**.

---

# Current Development Stage

Current milestone:

**Phase 1 — Zhihu Collection Export**

The only objective is to build a reliable exporter.

Do not optimize later stages before Phase 1 is stable.

Phase 1 prioritizes reliability over completeness.

---

# Environment

- Language: Python 3.11+
- Package manager: uv (preferred) or pip
- Storage: Local JSON files
- Database: None in Phase 1
- Authentication: Zhihu browser session cookie stored in `.env`
- Target platform: macOS and Linux (Windows compatibility is desirable)

See `.env.example` for all required and optional environment variables.

---

# Architecture

```text
CLI
      ↓
Exporter
      ↓
ZhihuClient
      ↓
JSONL
```

Modules should be replaceable without affecting the rest of the pipeline.

Do not introduce repository patterns, plugin systems, dependency injection frameworks, event buses, databases, or Phase 2 AI abstractions during Phase 1.

---

# Raw Data Rules

Never invent fields.

Always follow `schema/article.v1.json`.

Raw exported data is immutable.

Schema evolution rules:

- Increment `schema_version` whenever the schema changes.
- Never rewrite historical exports automatically.
- Backward compatibility is preferred whenever practical.

`raw_html` is disabled by default.

Store it only when:

- content extraction fails
- HTML structure is explicitly requested
- debugging is required

It may be removed after content verification.

---

# AI Responsibilities

AI is responsible for:

- Summarization
- Classification
- Tag generation
- Topic clustering
- Duplicate detection
- Knowledge linking
- Reading priority recommendation

AI-generated data must always be stored separately from raw source data.

---

# Coding Guidelines

- Write readable and maintainable code.
- Use type hints where appropriate.
- Keep modules focused on a single responsibility.
- Prefer configuration over hard-coded values.
- Add logging for important operations.
- Support resumable execution.
- Handle failures gracefully.
- Design modules for future extension.

---

# Do NOT

- Do NOT overwrite or modify raw exported data.
- Do NOT write AI summaries back into the original JSON.
- Do NOT introduce a database during Phase 1.
- Do NOT collect data outside the user's authorized scope.
- Do NOT tightly couple exporter logic with AI processing logic.
- Do NOT optimize for future phases before Phase 1 is complete.

---

# Success Criteria for Phase 1

Phase 1 is considered complete only if:

1. Zhihu collections can be exported reliably.
2. Exported item count matches the source collection.
3. Output conforms to `schema/article.v1.json`.
4. Export supports interruption and resume.
5. Required metadata is preserved.

After these conditions are met, development may proceed to AI processing (Phase 2).
