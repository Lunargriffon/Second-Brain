# Cross-Domain Job Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有仅支持单文档的 JobQueue 扩展为兼容 document/entity/fact/fact_relation/synthesis 范围的可恢复、可审计、幂等任务调度基础，同时保持 article 派生管线行为不变。

**Architecture:** schema v8 重建 `jobs`、`job_events`、`job_scopes`，用规范化 scope 集合的 SHA-256 `scope_hash` 取代 `document_id` 作为幂等身份的一部分；旧 article 任务自动迁移为单 document scope。`JobQueue` 保留原 `enqueue()` 兼容入口，新增类型化 `enqueue_scoped()` 和 scope 查询，并提供“校验 lease → 写领域结果 → 标记成功”的同连接原子发布边界。

**Tech Stack:** Python 3.12、SQLite、标准库 `sqlite3` / `hashlib` / `json` / `dataclasses`、pytest、Ruff。

---

## 范围

本计划实现：

- schema v8 的跨域 job identity 与安全升级；
- document/entity/fact/fact_relation/synthesis scope 的规范化和值对象；
- 旧 `enqueue(job_type, document_id, ...)` 完全兼容；
- scope-based enqueue、claim、读取、审计事件；
- lease 所有权约束下的原子领域写入与成功提交；
- article pipeline 和 CLI 的无回归验证；
- 为下一阶段 Fact extraction worker 提供稳定调度接口。

本计划不实现：

- LLM Fact/Entity 抽取 prompt 或 provider 调用；
- 实体链接、冲突检测 worker；
- Synthesis/`pkb think`；
- CLI/MCP/Obsidian 新入口；
- dream cycle 调度器；
- 修改 Fact/Entity 已冻结生命周期语义。

## 冻结接口决定

1. `pipeline_version` 仍表示 worker/pipeline 行为版本；LLM 的 `prompt_version` 仍只写入 derivation，不混用。
2. job 幂等键为 `(job_type, scope_hash, input_hash, pipeline_version)`。
3. `scope_hash` 只由规范化 scope 集合决定，不包含 job type、input hash 或 pipeline version。
4. scope 按 `(scope_role, ordinal, scope_type, scope_id)` 排序后编码为紧凑 JSON，再计算 SHA-256。
5. 每个 job 恰好一个 `anchor` scope；`input/context/output` 可为零或多个。
6. `document_id` 变为兼容投影：document anchor 时等于该 document id；非 document anchor 时为 `NULL`。
7. 历史 article job 自动获得 document anchor，已有 job id 与 job event id 保持不变。
8. worker 只能通过同一个 `JobQueue.connection` 在 lease 所有权检查后发布领域写入；失去 lease 时不得产生领域行。

## File Map

| Path | Responsibility |
|---|---|
| `src/pkb/knowledge/schema_v8.py` | jobs/job_events/job_scopes 的 v8 rebuild SQL、约束和索引 |
| `src/pkb/knowledge/migrations.py` | 注册 migration v8，保持逐版本升级 |
| `src/pkb/knowledge/job_scopes.py` | 供 migration 与 derive 共用的 `JobScope`、规范化、排序与 `scope_hash` |
| `src/pkb/derive/jobs.py` | 兼容 enqueue、scoped enqueue、claim/read scopes、原子 publish |
| `src/pkb/derive/pipeline.py` | article pipeline 使用原子发布边界，外部行为不变 |
| `src/pkb/derive/cli.py` | 只做类型兼容调整；不新增用户命令 |
| `tests/test_cross_domain_job_migrations.py` | v7→v8 数据保留、DDL 约束、幂等键测试 |
| `tests/test_job_scopes.py` | scope 规范化、hash、enqueue/claim 行为 |
| `tests/test_derivation_jobs.py` | 旧 API、lease、dead-letter 回归 |
| `tests/test_derivation_pipeline.py` | article 原子发布与失 lease回归 |
| `tests/test_fact_entity_acceptance.py` | 用新原子发布边界替换现有 lease 组合测试 |
| `docs/second-brain-operations.md` | 记录 v8 调度能力及仍未启用的 worker |

### Task 1: Freeze Scope Value Semantics

**Files:**
- Create: `src/pkb/knowledge/job_scopes.py`
- Create: `tests/test_job_scopes.py`

- [ ] **Step 1: Write failing normalization and hash tests**

```python
from pkb.knowledge.job_scopes import JobScope, canonical_scope_json, scope_hash


def test_scope_hash_is_order_independent_but_role_and_ordinal_sensitive():
    scopes = (
        JobScope("fact", "42", "input", 0),
        JobScope("entity", "7", "anchor", 0),
    )
    assert scope_hash(scopes) == scope_hash(tuple(reversed(scopes)))
    assert scope_hash(scopes) != scope_hash(
        (JobScope("fact", "42", "context", 0), scopes[1])
    )
    assert canonical_scope_json(scopes) == (
        '[{"ordinal":0,"role":"anchor","type":"entity","id":"7"},'
        '{"ordinal":0,"role":"input","type":"fact","id":"42"}]'
    )
```

- [ ] **Step 2: Write failing validation tests**

```python
import pytest


@pytest.mark.parametrize("scope_type", ["", "document ", "unknown"])
def test_scope_type_must_be_frozen_value(scope_type):
    with pytest.raises(ValueError):
        JobScope(scope_type, "1", "anchor", 0)


def test_scope_set_requires_exactly_one_anchor_and_unique_role_ordinal():
    with pytest.raises(ValueError, match="exactly one anchor"):
        scope_hash((JobScope("fact", "1", "input", 0),))
    with pytest.raises(ValueError, match="duplicate scope role/ordinal"):
        scope_hash((
            JobScope("entity", "1", "anchor", 0),
            JobScope("fact", "2", "input", 0),
            JobScope("fact", "3", "input", 0),
        ))
```

- [ ] **Step 3: Run tests and verify import failure**

Run: `python -m pytest tests/test_job_scopes.py -v --basetemp=.pytest-tmp-job-scope-task1`

Expected: FAIL because `pkb.knowledge.job_scopes` does not exist.

- [ ] **Step 4: Implement immutable scope semantics**

```python
"""Canonical cross-domain job scopes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

SCOPE_TYPES = frozenset({"document", "entity", "fact", "fact_relation", "synthesis"})
SCOPE_ROLES = frozenset({"anchor", "input", "output", "context"})


@dataclass(frozen=True, slots=True)
class JobScope:
    scope_type: str
    scope_id: str
    scope_role: str
    ordinal: int = 0

    def __post_init__(self) -> None:
        if self.scope_type not in SCOPE_TYPES:
            raise ValueError(f"invalid scope type: {self.scope_type!r}")
        if self.scope_role not in SCOPE_ROLES:
            raise ValueError(f"invalid scope role: {self.scope_role!r}")
        if not self.scope_id or self.scope_id != self.scope_id.strip():
            raise ValueError("scope_id must be non-empty and already trimmed")
        if self.ordinal < 0:
            raise ValueError("scope ordinal must be non-negative")


def canonical_scopes(scopes: Iterable[JobScope]) -> tuple[JobScope, ...]:
    values = tuple(scopes)
    if sum(scope.scope_role == "anchor" for scope in values) != 1:
        raise ValueError("scope set requires exactly one anchor")
    positions = [(scope.scope_role, scope.ordinal) for scope in values]
    if len(positions) != len(set(positions)):
        raise ValueError("duplicate scope role/ordinal")
    return tuple(sorted(values, key=lambda s: (s.scope_role, s.ordinal, s.scope_type, s.scope_id)))


def canonical_scope_json(scopes: Iterable[JobScope]) -> str:
    payload = [
        {"ordinal": s.ordinal, "role": s.scope_role, "type": s.scope_type, "id": s.scope_id}
        for s in canonical_scopes(scopes)
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def scope_hash(scopes: Iterable[JobScope]) -> str:
    return hashlib.sha256(canonical_scope_json(scopes).encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/test_job_scopes.py -v --basetemp=.pytest-tmp-job-scope-task1`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/pkb/knowledge/job_scopes.py tests/test_job_scopes.py
git commit -m "feat: define canonical cross-domain job scopes"
```

### Task 2: Add Schema v8 Cross-Domain Job Tables

**Files:**
- Create: `src/pkb/knowledge/schema_v8.py`
- Modify: `src/pkb/knowledge/migrations.py`
- Create: `tests/test_cross_domain_job_migrations.py`
- Modify: `tests/test_knowledge_migrations.py`

- [ ] **Step 1: Write a failing fresh-schema contract test**

```python
def test_v8_jobs_support_non_document_anchor(knowledge_connection):
    columns = {
        row[1]: row for row in knowledge_connection.execute("PRAGMA table_info(jobs)")
    }
    assert columns["document_id"][3] == 0
    assert columns["scope_hash"][3] == 1
    assert knowledge_connection.execute("PRAGMA user_version").fetchone()[0] == 8
```

- [ ] **Step 2: Run and verify schema version failure**

Run: `python -m pytest tests/test_cross_domain_job_migrations.py -v --basetemp=.pytest-tmp-job-scope-task2`

Expected: FAIL because current schema version is 7 and `scope_hash` is absent.

- [ ] **Step 3: Add the complete v8 rebuild statements**

Create `src/pkb/knowledge/schema_v8.py` with `KNOWLEDGE_SCHEMA_V8: tuple[str, ...]`. The tuple must execute this exact logical sequence in the migration transaction:

```sql
CREATE TABLE jobs_v8 (
    id INTEGER PRIMARY KEY,
    job_type TEXT NOT NULL CHECK(length(trim(job_type)) > 0),
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    scope_hash TEXT NOT NULL CHECK(length(scope_hash) = 64),
    input_hash TEXT NOT NULL CHECK(length(input_hash) > 0),
    pipeline_version TEXT NOT NULL CHECK(length(trim(pipeline_version)) > 0),
    status TEXT NOT NULL CHECK(status IN (
        'pending','running','succeeded','failed','dead-letter'
    )),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    worker_id TEXT,
    leased_at TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(job_type, scope_hash, input_hash, pipeline_version),
    CHECK(
        (status = 'running' AND worker_id IS NOT NULL AND leased_at IS NOT NULL
         AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL)
        OR
        (status <> 'running' AND worker_id IS NULL AND leased_at IS NULL
         AND lease_expires_at IS NULL AND heartbeat_at IS NULL)
    )
);

CREATE TABLE job_events_v8 (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs_v8(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    worker_id TEXT,
    details_json TEXT CHECK(details_json IS NULL OR json_valid(details_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE job_scopes_v8 (
    job_id INTEGER NOT NULL REFERENCES jobs_v8(id) ON DELETE CASCADE,
    scope_type TEXT NOT NULL CHECK(scope_type IN (
        'document','entity','fact','fact_relation','synthesis'
    )),
    scope_id TEXT NOT NULL CHECK(length(scope_id) > 0),
    scope_role TEXT NOT NULL CHECK(scope_role IN ('anchor','input','output','context')),
    ordinal INTEGER NOT NULL DEFAULT 0 CHECK(ordinal >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(job_id, scope_type, scope_id, scope_role),
    UNIQUE(job_id, scope_role, ordinal)
);
```

For every old job, compute the document-scope hash in SQL as a temporary migration value only if a registered deterministic SQLite SHA-256 function is available; otherwise migration v8 must be a Python migration callable (Task 3) and must not use a different hash algorithm. Copy job ids and event ids unchanged, create one `(document, CAST(document_id AS TEXT), anchor, 0)` scope per old job, drop children before parent, rename `_v8` tables, then recreate:

```sql
CREATE INDEX idx_jobs_claim ON jobs(status, lease_expires_at, created_at);
CREATE INDEX idx_jobs_document ON jobs(document_id);
CREATE INDEX idx_jobs_scope_hash ON jobs(scope_hash);
CREATE INDEX idx_job_events_job ON job_events(job_id, created_at);
CREATE INDEX idx_job_scopes_lookup ON job_scopes(scope_type, scope_id, scope_role, job_id);
CREATE UNIQUE INDEX uq_job_anchor ON job_scopes(job_id) WHERE scope_role='anchor';
```

- [ ] **Step 4: Register schema version 8**

Update `SCHEMA_VERSION = 8` and register migration 8. Do not edit migrations 1–7.

- [ ] **Step 5: Run the fresh schema test**

Run: `python -m pytest tests/test_cross_domain_job_migrations.py::test_v8_jobs_support_non_document_anchor -v --basetemp=.pytest-tmp-job-scope-task2`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/pkb/knowledge/schema_v8.py src/pkb/knowledge/migrations.py tests/test_cross_domain_job_migrations.py tests/test_knowledge_migrations.py
git commit -m "feat: add cross-domain job schema v8"
```

### Task 3: Make v7-to-v8 Upgrade Deterministic and Lossless

**Files:**
- Modify: `src/pkb/knowledge/migrations.py`
- Modify: `src/pkb/knowledge/schema_v8.py`
- Modify: `tests/test_cross_domain_job_migrations.py`

- [ ] **Step 1: Write the preservation test**

```python
def test_v7_to_v8_preserves_job_and_event_ids_and_adds_document_anchor(v7_database):
    before_jobs = rows(v7_database, "SELECT * FROM jobs ORDER BY id")
    before_events = rows(v7_database, "SELECT * FROM job_events ORDER BY id")

    migrate(v7_database)

    after_jobs = rows(v7_database, "SELECT * FROM jobs ORDER BY id")
    after_events = rows(v7_database, "SELECT * FROM job_events ORDER BY id")
    assert [row["id"] for row in after_jobs] == [row["id"] for row in before_jobs]
    assert after_events == before_events
    assert dict(rows(v7_database, "SELECT * FROM job_scopes")[0]) | {} == {
        "job_id": before_jobs[0]["id"],
        "scope_type": "document",
        "scope_id": str(before_jobs[0]["document_id"]),
        "scope_role": "anchor",
        "ordinal": 0,
        "created_at": rows(v7_database, "SELECT * FROM job_scopes")[0]["created_at"],
    }
```

- [ ] **Step 2: Add migration callable support without changing old migrations**

Define:

```python
MigrationStep = tuple[str, ...] | Callable[[sqlite3.Connection], None]


def _apply_migration(connection: sqlite3.Connection, migration: MigrationStep) -> None:
    if callable(migration):
        migration(connection)
        return
    for statement in migration:
        connection.execute(statement)
```

The v8 callable must import `JobScope` and `scope_hash` from `pkb.knowledge.job_scopes`, populate `jobs_v8` row by row with the canonical document anchor hash, populate scopes/events, verify copied counts, then swap tables. Keeping the helper below `pkb.knowledge` avoids a forbidden knowledge→derive dependency. Any exception rolls back the whole version transition and leaves `user_version=7`.

- [ ] **Step 3: Add rollback and FK integrity tests**

```python
def test_v8_upgrade_rolls_back_on_copy_failure(v7_database, monkeypatch):
    monkeypatch.setattr(migrations, "_copy_v8_jobs", lambda _connection: (_ for _ in ()).throw(RuntimeError("copy failed")))
    with pytest.raises(RuntimeError, match="copy failed"):
        migrate(v7_database)
    assert v7_database.execute("PRAGMA user_version").fetchone()[0] == 7
    assert v7_database.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_v8_upgrade_passes_integrity_and_foreign_key_checks(v7_database):
    migrate(v7_database)
    assert v7_database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert v7_database.execute("PRAGMA foreign_key_check").fetchall() == []
```

- [ ] **Step 4: Run migration suites**

Run: `python -m pytest tests/test_cross_domain_job_migrations.py tests/test_knowledge_migrations.py tests/test_fact_entity_migrations.py -v --basetemp=.pytest-tmp-job-scope-task3`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/knowledge/migrations.py src/pkb/knowledge/schema_v8.py tests/test_cross_domain_job_migrations.py
git commit -m "test: prove lossless cross-domain job migration"
```

### Task 4: Add Scoped Enqueue While Preserving the Legacy API

**Files:**
- Modify: `src/pkb/derive/jobs.py`
- Modify: `tests/test_job_scopes.py`
- Modify: `tests/test_derivation_jobs.py`

- [ ] **Step 1: Write failing scoped enqueue tests**

```python
def test_enqueue_scoped_is_idempotent_for_reordered_scope_input(job_queue):
    scopes = (
        JobScope("entity", "7", "anchor", 0),
        JobScope("document", "2", "input", 0),
    )
    first = job_queue.enqueue_scoped("fact-extraction", scopes, "input-a", "fact-v1")
    second = job_queue.enqueue_scoped(
        "fact-extraction", tuple(reversed(scopes)), "input-a", "fact-v1"
    )
    assert first == second
    assert job_queue.get(first).document_id is None
    assert job_queue.scopes(first) == canonical_scopes(scopes)


def test_legacy_enqueue_creates_document_anchor(job_queue):
    job_id = job_queue.enqueue("article", 1, "hash", "article-v1")
    assert job_queue.scopes(job_id) == (JobScope("document", "1", "anchor", 0),)
    assert job_queue.get(job_id).document_id == 1
```

- [ ] **Step 2: Run and verify missing API failure**

Run: `python -m pytest tests/test_job_scopes.py tests/test_derivation_jobs.py -v --basetemp=.pytest-tmp-job-scope-task4`

Expected: FAIL because `enqueue_scoped()` and `scopes()` do not exist and `Job.document_id` is not optional.

- [ ] **Step 3: Update the Job value object and enqueue APIs**

Use these signatures:

```python
@dataclass(frozen=True)
class Job:
    id: int
    job_type: str
    document_id: int | None
    scope_hash: str
    input_hash: str
    pipeline_version: str
    status: str
    attempts: int
    worker_id: str | None
    leased_at: datetime | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    error: str | None


def enqueue(self, job_type: str, document_id: int, input_hash: str, pipeline_version: str) -> int:
    return self.enqueue_scoped(
        job_type,
        (JobScope("document", str(document_id), "anchor", 0),),
        input_hash,
        pipeline_version,
    )


def enqueue_scoped(
    self,
    job_type: str,
    scopes: Iterable[JobScope],
    input_hash: str,
    pipeline_version: str,
) -> int:
    values = canonical_scopes(scopes)
    identity = scope_hash(values)
    anchor = next(scope for scope in values if scope.scope_role == "anchor")
    document_id = int(anchor.scope_id) if anchor.scope_type == "document" else None
    # BEGIN IMMEDIATE; INSERT OR IGNORE jobs; SELECT by the v8 unique key;
    # insert every scope with INSERT OR IGNORE; verify persisted scopes exactly
    # equal values; append one enqueued event only for a newly inserted job; COMMIT.
```

Reject empty `job_type`, `input_hash`, `pipeline_version`, non-decimal document anchor ids, and a document anchor that references a missing document before inserting the job.

- [ ] **Step 4: Implement scope reads**

```python
def scopes(self, job_id: int) -> tuple[JobScope, ...]:
    rows = self.connection.execute(
        """SELECT scope_type, scope_id, scope_role, ordinal
           FROM job_scopes WHERE job_id=?
           ORDER BY scope_role, ordinal, scope_type, scope_id""",
        (job_id,),
    ).fetchall()
    if not rows and self.connection.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None:
        raise KeyError(job_id)
    return canonical_scopes(JobScope(row[0], row[1], row[2], int(row[3])) for row in rows)
```

- [ ] **Step 5: Run focused and legacy tests**

Run: `python -m pytest tests/test_job_scopes.py tests/test_derivation_jobs.py -v --basetemp=.pytest-tmp-job-scope-task4`

Expected: PASS; all existing JobQueue tests remain unchanged except assertions that intentionally inspect scopes.

- [ ] **Step 6: Commit**

```powershell
git add src/pkb/derive/jobs.py tests/test_job_scopes.py tests/test_derivation_jobs.py
git commit -m "feat: enqueue idempotent scoped jobs"
```

### Task 5: Add Scope-Aware Claim Filtering and Audit Details

**Files:**
- Modify: `src/pkb/derive/jobs.py`
- Modify: `tests/test_job_scopes.py`

- [ ] **Step 1: Write failing claim filter tests**

```python
def test_claim_can_filter_by_type_and_anchor_scope(job_queue):
    entity_job = job_queue.enqueue_scoped(
        "fact-maintenance", (JobScope("entity", "7", "anchor", 0),), "a", "v1"
    )
    job_queue.enqueue_scoped(
        "fact-maintenance", (JobScope("fact", "9", "anchor", 0),), "b", "v1"
    )
    claimed = job_queue.claim(
        "worker", job_type="fact-maintenance", anchor_type="entity"
    )
    assert claimed is not None and claimed.id == entity_job


def test_claim_event_captures_scope_hash(job_queue):
    job_id = job_queue.enqueue_scoped(
        "fact-maintenance", (JobScope("entity", "7", "anchor", 0),), "a", "v1"
    )
    claimed = job_queue.claim("worker")
    event = job_queue.events(job_id)[-1]
    assert json.loads(event["details_json"])["scope_hash"] == claimed.scope_hash
```

- [ ] **Step 2: Extend claim with an indexed EXISTS filter**

Use the signature:

```python
def claim(
    self,
    worker_id: str,
    *,
    job_type: str | None = None,
    anchor_type: str | None = None,
    now: datetime | None = None,
    lease: timedelta = DEFAULT_LEASE,
) -> Job | None:
```

When `anchor_type` is supplied, validate it against `SCOPE_TYPES` and add:

```sql
AND EXISTS (
    SELECT 1 FROM job_scopes AS scope
    WHERE scope.job_id=jobs.id
      AND scope.scope_role='anchor'
      AND scope.scope_type=?
)
```

Keep ordering, lease reclaim, attempts, dead-letter, and `BEGIN IMMEDIATE` semantics unchanged. Include `scope_hash` in `enqueued`, `claimed`, `lease_expired`, and `dead_lettered` event details without removing existing keys.

- [ ] **Step 3: Run queue tests**

Run: `python -m pytest tests/test_job_scopes.py tests/test_derivation_jobs.py -v --basetemp=.pytest-tmp-job-scope-task5`

Expected: PASS.

- [ ] **Step 4: Commit**

```powershell
git add src/pkb/derive/jobs.py tests/test_job_scopes.py
git commit -m "feat: claim jobs by anchor scope"
```

### Task 6: Add Lease-Owned Atomic Publication

**Files:**
- Modify: `src/pkb/derive/jobs.py`
- Modify: `tests/test_derivation_jobs.py`
- Modify: `tests/test_fact_entity_acceptance.py`

- [ ] **Step 1: Write failing atomic publication tests**

```python
def test_publish_and_succeed_is_one_transaction(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = job_queue.enqueue("article", 1, "hash", "v1")
    job_queue.claim("owner", now=now)

    job_queue.publish_and_succeed(
        job_id,
        "owner",
        lambda connection: connection.execute(
            "UPDATE documents SET title='published' WHERE id=1"
        ),
        now=now + timedelta(minutes=1),
    )

    assert job_queue.get(job_id).status == "succeeded"
    assert job_queue.connection.execute("SELECT title FROM documents WHERE id=1").fetchone()[0] == "published"


def test_lost_lease_blocks_publication_before_domain_write(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = job_queue.enqueue("article", 1, "hash", "v1")
    job_queue.claim("old", now=now, lease=timedelta(seconds=1))
    job_queue.claim("new", now=now + timedelta(seconds=2))

    with pytest.raises(LeaseOwnershipError):
        job_queue.publish_and_succeed(
            job_id,
            "old",
            lambda connection: connection.execute(
                "UPDATE documents SET title='must-not-persist' WHERE id=1"
            ),
            now=now + timedelta(seconds=2),
        )
    assert job_queue.connection.execute("SELECT title FROM documents WHERE id=1").fetchone()[0] is None


def test_publication_exception_rolls_back_domain_write_and_job_state(job_queue):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_id = job_queue.enqueue("article", 1, "hash", "v1")
    job_queue.claim("owner", now=now)

    def broken_publish(connection):
        connection.execute("UPDATE documents SET title='rolled-back' WHERE id=1")
        raise RuntimeError("publication failed")

    with pytest.raises(RuntimeError, match="publication failed"):
        job_queue.publish_and_succeed(
            job_id, "owner", broken_publish, now=now + timedelta(minutes=1)
        )
    assert job_queue.connection.execute("SELECT title FROM documents WHERE id=1").fetchone()[0] is None
    assert job_queue.get(job_id).status == "running"
```

- [ ] **Step 2: Implement the transactional API**

```python
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def publish_and_succeed(
    self,
    job_id: int,
    worker_id: str,
    publish: Callable[[sqlite3.Connection], T],
    *,
    now: datetime | None = None,
) -> T:
    timestamp = _serialize(_utc(now))
    self.connection.execute("BEGIN IMMEDIATE")
    try:
        self._require_owner(job_id, worker_id, timestamp)
        result = publish(self.connection)
        self._require_owner(job_id, worker_id, timestamp)
        self.connection.execute(
            """UPDATE jobs SET status='succeeded', worker_id=NULL, leased_at=NULL,
               lease_expires_at=NULL, heartbeat_at=NULL, error=NULL, updated_at=?
               WHERE id=?""",
            (timestamp, job_id),
        )
        self._event(job_id, "succeeded", worker_id)
        self.connection.commit()
        return result
    except BaseException:
        self.connection.rollback()
        raise
```

Document that the callback must use the supplied connection and must not commit, roll back, close, or open an independent write connection.

- [ ] **Step 3: Update the Fact lifecycle lease acceptance scenario**

Replace its separate `queue.succeed()` plus repository write sequence with a callback that constructs the repository over the supplied connection or invokes a connection-level publication helper. The assertion remains: an expired/non-owner lease produces no accepted Fact or relation.

- [ ] **Step 4: Run lease and acceptance tests**

Run: `python -m pytest tests/test_derivation_jobs.py tests/test_fact_entity_acceptance.py -v --basetemp=.pytest-tmp-job-scope-task6`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/derive/jobs.py tests/test_derivation_jobs.py tests/test_fact_entity_acceptance.py
git commit -m "feat: publish job results inside owned lease transaction"
```

### Task 7: Move Article Pipeline to the Atomic Boundary

**Files:**
- Modify: `src/pkb/derive/pipeline.py`
- Modify: `tests/test_derivation_pipeline.py`

- [ ] **Step 1: Write a failing rollback test at each stage hook**

```python
@pytest.mark.parametrize("stage", ["derivation", "projection"])
def test_article_publication_rolls_back_when_stage_fails(database, provider, tmp_path, stage):
    pipeline = DerivationPipeline(
        database,
        provider,
        run_log=tmp_path / "run.jsonl",
        stage_hook=lambda current: (_ for _ in ()).throw(RuntimeError("stop"))
        if current == stage else None,
    )
    with pytest.raises(RuntimeError, match="stop"):
        pipeline.run(limit=1)
    assert rows(database, "SELECT * FROM derivations") == []
    assert rows(database, "SELECT * FROM document_tags") == []
    assert rows(database, "SELECT status FROM jobs")[0]["status"] == "running"
```

- [ ] **Step 2: Refactor only the trusted publication section**

After provider response validation, call:

```python
def publish(connection: sqlite3.Connection) -> None:
    self._insert_derivation(connection, job, document, derivation_id, payload)
    self.stage_hook("derivation")
    self._project(connection, job.document_id, derivation_id, payload)
    self.stage_hook("projection")

queue.publish_and_succeed(job.id, self.worker_id, publish)
```

Refactor `_project` so every SQL statement uses the supplied connection and no nested context manager commits early. Update search projection through a connection-level helper that accepts `commit=False`, or issue the existing deterministic projection SQL directly inside the callback. Do not change provider retry behavior or accepted-derivation reuse semantics.

- [ ] **Step 3: Prove expired lease cannot publish validated provider output**

Retain the existing slow-provider lease test and assert all of these remain absent after ownership loss: derivation, AI tags/topics, search projection change, succeeded event.

- [ ] **Step 4: Run pipeline and CLI regressions**

Run: `python -m pytest tests/test_derivation_pipeline.py tests/test_derivation_cli.py tests/test_derivation_jobs.py -v --basetemp=.pytest-tmp-job-scope-task7`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/pkb/derive/pipeline.py tests/test_derivation_pipeline.py
git commit -m "refactor: atomically publish article derivations"
```

### Task 8: Prove Cross-Domain Scheduling Acceptance

**Files:**
- Create: `tests/test_cross_domain_job_acceptance.py`
- Modify: `docs/second-brain-operations.md`

- [ ] **Step 1: Add the five acceptance scenarios**

The file must contain these five scenarios with real database rows, not mocks. Reuse a local `database` fixture that migrates a temporary database and inserts documents 1 and 2:

```python
def test_document_article_job_remains_backward_compatible(database):
    with JobQueue(database) as queue:
        job_id = queue.enqueue("article", 1, "source-1", "article-v1")
        assert queue.scopes(job_id) == (JobScope("document", "1", "anchor", 0),)
        assert queue.claim("article-worker", job_type="article").id == job_id


def test_entity_anchor_can_reference_multiple_document_inputs(database):
    scopes = (
        JobScope("entity", "7", "anchor", 0),
        JobScope("document", "1", "input", 0),
        JobScope("document", "2", "input", 1),
    )
    with JobQueue(database) as queue:
        job_id = queue.enqueue_scoped("entity-maintenance", scopes, "entity-7", "v1")
        assert queue.scopes(job_id) == canonical_scopes(scopes)
        assert queue.get(job_id).document_id is None


def test_fact_relation_anchor_is_idempotent_across_scope_order(database):
    scopes = (
        JobScope("fact_relation", "11", "anchor", 0),
        JobScope("fact", "3", "input", 0),
        JobScope("fact", "4", "input", 1),
    )
    with JobQueue(database) as queue:
        first = queue.enqueue_scoped("relation-validation", scopes, "relation-11", "v1")
        second = queue.enqueue_scoped(
            "relation-validation", tuple(reversed(scopes)), "relation-11", "v1"
        )
        assert first == second
        assert len(queue.events(first)) == 1


def test_expired_cross_domain_job_is_reclaimed_and_old_worker_cannot_publish(database):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with JobQueue(database) as queue:
        job_id = queue.enqueue_scoped(
            "entity-maintenance", (JobScope("entity", "7", "anchor", 0),), "a", "v1"
        )
        queue.claim("old", now=now, lease=timedelta(seconds=1))
        queue.claim("new", now=now + timedelta(seconds=2))
        with pytest.raises(LeaseOwnershipError):
            queue.publish_and_succeed(
                job_id,
                "old",
                lambda connection: connection.execute(
                    "INSERT INTO knowledge_events "
                    "(object_type, object_id, event_type, actor_type, reason, job_id) "
                    "VALUES ('entity', 7, 'rebuilt', 'system', 'acceptance', ?)",
                    (job_id,),
                ),
                now=now + timedelta(seconds=2),
            )
        assert queue.connection.execute(
            "SELECT COUNT(*) FROM knowledge_events WHERE job_id=?", (job_id,)
        ).fetchone()[0] == 0


def test_v7_database_upgrades_and_existing_article_pipeline_still_succeeds(
    v7_article_database, provider, tmp_path
):
    pipeline = DerivationPipeline(
        v7_article_database, provider, run_log=tmp_path / "article-run.jsonl"
    )
    result = pipeline.run(limit=1)
    assert result.succeeded == 1
    with sqlite3.connect(v7_article_database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        assert connection.execute(
            "SELECT scope_type, scope_id, scope_role FROM job_scopes"
        ).fetchone() == ("document", "1", "anchor")
```

Each scenario asserts job row, exact ordered `job_scopes`, event sequence, and final status. The entity/fact relation scenarios may publish a `knowledge_events` audit row through `publish_and_succeed`; they must not invoke an LLM or invent extraction behavior.

- [ ] **Step 2: Run acceptance tests and fix only contract gaps**

Run: `python -m pytest tests/test_cross_domain_job_acceptance.py -v --basetemp=.pytest-tmp-job-scope-task8`

Expected: PASS.

- [ ] **Step 3: Update operations status**

Add a concise “Cross-domain Job Scope Status” subsection stating:

- schema v8 and scoped JobQueue are enabled;
- legacy article jobs remain supported;
- supported scope types and the exactly-one-anchor rule;
- job scope rows are infrastructure/audit data and must not be manually edited;
- Fact extraction, maintenance workers, synthesis and dream cycle remain disabled until their own reviewed plans land.

- [ ] **Step 4: Run adjacent architecture checks**

Run: `python -m pytest tests/test_cross_domain_job_acceptance.py tests/test_fact_entity_acceptance.py tests/test_package_boundaries.py tests/test_repository_hygiene.py -v --basetemp=.pytest-tmp-job-scope-task8-adjacent`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_cross_domain_job_acceptance.py docs/second-brain-operations.md
git commit -m "test: accept cross-domain job scheduling"
```

### Task 9: Final Verification

**Files:**
- Verify all files listed above

- [ ] **Step 1: Run static checks on touched files**

Run:

```powershell
python -m ruff check src/pkb/knowledge/job_scopes.py src/pkb/derive/jobs.py src/pkb/derive/pipeline.py src/pkb/knowledge/schema_v8.py src/pkb/knowledge/migrations.py tests/test_job_scopes.py tests/test_cross_domain_job_migrations.py tests/test_cross_domain_job_acceptance.py tests/test_derivation_jobs.py tests/test_derivation_pipeline.py tests/test_fact_entity_acceptance.py
git diff --check
```

Expected: both commands exit 0.

- [ ] **Step 2: Run the focused foundation suite**

Run:

```powershell
python -m pytest tests/test_cross_domain_job_migrations.py tests/test_job_scopes.py tests/test_derivation_jobs.py tests/test_derivation_pipeline.py tests/test_derivation_cli.py tests/test_cross_domain_job_acceptance.py tests/test_fact_entity_acceptance.py -q --basetemp=.pytest-tmp-job-scope-final
```

Expected: PASS.

- [ ] **Step 3: Run full regression**

Run: `python -m pytest -q --basetemp=.pytest-tmp-job-scope-full`

Expected: all tests PASS; skips must match the repository baseline.

- [ ] **Step 4: Check database integrity on fresh and upgraded databases**

For both a fresh v8 database and the v7 upgrade fixture, assert:

```sql
PRAGMA integrity_check;     -- exactly one row: ok
PRAGMA foreign_key_check;   -- zero rows
```

- [ ] **Step 5: Confirm scope boundaries**

Run:

```powershell
git diff --name-only
git status --short
```

Expected: no provider prompt, Fact extraction worker, MCP, wiki renderer, frontend, source adapter, or Douyin file was changed.

- [ ] **Step 6: Commit verification-only fixes if any**

If verification required a code correction, stage the exact corrected implementation file and its exact regression test. For example, if the correction is in `jobs.py`:

```powershell
git add src/pkb/derive/jobs.py tests/test_derivation_jobs.py
git commit -m "fix: close cross-domain job regression"
```

If no correction was needed, do not create an empty commit.

## Completion Gate

本计划仅在以下条件全部满足时完成：

- v7→v8 保留所有 job/event id 和审计记录；
- 每个历史 job 获得正确 document anchor；
- 新 job 以 scope hash 幂等，scope 顺序不影响身份；
- 非 document anchor 不需要伪造 `document_id`；
- article enqueue/claim/retry/dead-letter/CLI 全部无回归；
- 领域发布与 job succeeded 在同一事务内；
- lease 丢失时零领域写入；
- 完整性检查、专项测试和全量测试通过；
- 没有实现或暗中启用 Fact extraction、Synthesis 或 dream cycle。

## 后续计划入口

该计划完成后，下一份独立计划应是 **Fact Extraction Candidate Pipeline**：document text version 触发 `fact-extraction` scoped job，LLM 只生成 Entity/Mention/Fact/Evidence candidates，确定性 grounding 校验后写入 pending 状态；实体合并、Fact 发布、冲突裁决仍保持人工边界。
