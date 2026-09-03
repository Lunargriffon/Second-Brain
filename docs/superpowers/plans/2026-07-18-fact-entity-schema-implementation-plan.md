# Fact / Entity Knowledge Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不接入 LLM 抽取和跨域 JobQueue 的前提下，落地 Fact / Entity 知识层的 SQLite schema、不可变正文版本、事务型 repository、状态机约束和审计基础。

**Architecture:** 保留 `documents` 为当前标准化投影、`derivations` 为不可变运行审计，新建受约束的 Entity/Fact 领域表和 `document_text_versions`。数据库使用 CHECK、FK、partial UNIQUE、trigger 阻止非法行内状态；repository 使用 `BEGIN IMMEDIATE` 处理跨表前置条件、图环、合并撤销、失效传播和 append-only 事件。

**Tech Stack:** Python 3.12、SQLite、标准库 `sqlite3` / `unicodedata` / `hashlib` / `json`、pytest。

---

## Scope

本计划实现：

- schema v7 的知识领域表、索引、trigger 与 canonical view；
- 当前 document 的不可变标准化正文快照；
- Unicode NFKC、code-point offset 和 excerpt validator；
- Entity/Alias/Mention repository；
- Fact/Evidence/FactRelation repository；
- Entity 合并/撤销、Fact 状态转换、冲突标记和 append-only 审计；
- 生命周期规范 7 个离线验收场景。

本计划明确不实现：

- `jobs.document_id` nullable 重建、`scope_hash` 和 JobQueue 路由；
- LLM prompt、Fact 抽取 worker、实体链接评分；
- Synthesis/`pkb think`；
- CLI、MCP、Obsidian UI；
- 全库 normalization migration。

跨域 JobQueue 是下一份计划。Schema v7 可以创建 `derivation_scopes` 和 `job_scopes` 的 additive 表，但保持现有 `jobs` 结构和 JobQueue 行为不变；下一阶段再以独立 migration 重建 jobs 并启用 scope 幂等键。

## File Map

| Path | Responsibility |
|---|---|
| `src/pkb/knowledge/migrations.py` | schema v7 DDL、索引、trigger、view。 |
| `src/pkb/knowledge/schema_v7.py` | 保存经评审 schema 设计逐条转录得到的完整 v7 SQL tuple，避免继续膨胀 migrations.py。 |
| `src/pkb/knowledge/text_versions.py` | NFKC 文本快照、Unicode offset 与 excerpt 验证。 |
| `src/pkb/knowledge/entity_models.py` | Entity/Alias/Mention immutable value objects。 |
| `src/pkb/knowledge/entity_repository.py` | Entity 生命周期、alias、mention、canonical merge/undo。 |
| `src/pkb/knowledge/fact_models.py` | Typed object、Fact/Evidence/FactRelation value objects 与 fact key。 |
| `src/pkb/knowledge/fact_repository.py` | Fact 发布、证据、关系、失效与状态事务。 |
| `src/pkb/knowledge/repository.py` | document upsert 时维护当前 text version；不承载 Entity/Fact SQL。 |
| `tests/test_fact_entity_migrations.py` | DDL、约束、索引、trigger、升级测试。 |
| `tests/test_text_versions.py` | NFKC、code-point offset、版本保留测试。 |
| `tests/test_entity_repository.py` | Entity、alias、mention、merge/undo 测试。 |
| `tests/test_fact_repository.py` | Fact、Evidence、状态和 fact key 测试。 |
| `tests/test_fact_relation_repository.py` | conflict/supersedes/refines 测试。 |
| `tests/test_fact_entity_acceptance.py` | 7 个生命周期验收场景。 |

## Task 1: Add Schema v7 Knowledge Tables

**Files:**
- Create: `src/pkb/knowledge/schema_v7.py`
- Modify: `src/pkb/knowledge/migrations.py`
- Modify: `tests/test_knowledge_migrations.py`
- Create: `tests/test_fact_entity_migrations.py`

- [ ] **Step 1: Write the failing table and column contract test**

```python
import sqlite3

from pkb.knowledge.migrations import migrate


KNOWLEDGE_TABLES = {
    "document_text_versions",
    "entities",
    "entity_aliases",
    "entity_mentions",
    "facts",
    "fact_evidence",
    "fact_relations",
    "fact_relation_evidence",
    "entity_merge_events",
    "knowledge_events",
    "derivation_scopes",
    "job_scopes",
}


def test_v7_creates_fact_entity_schema(tmp_path):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    migrate(connection)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert KNOWLEDGE_TABLES <= tables
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run: `python -m pytest tests/test_fact_entity_migrations.py::test_v7_creates_fact_entity_schema -v`

Expected: FAIL because schema version is 6 and the tables do not exist.

- [ ] **Step 3: Add the complete reviewed v7 SQL module**

Create `src/pkb/knowledge/schema_v7.py`. Define one tuple whose entries are, in order, every `CREATE TABLE`, index, trigger, and view statement from sections 4, 5, 8, 10, 11, and 12 of `docs/superpowers/specs/2026-07-19-fact-entity-schema-design.md`:

```python
"""Reviewed SQLite v7 statements for the Fact/Entity knowledge foundation."""

from __future__ import annotations

KNOWLEDGE_SCHEMA_V7: tuple[str, ...] = (
    # Exact reviewed SQL statements, one statement per tuple item.
)
```

The tuple must contain exactly 12 `CREATE TABLE` statements, both reviewed view statements, every reviewed index, `facts_before_publish`, `fact_relations_before_publish`, `facts_no_delete`, and both append-only `knowledge_events` triggers. Do not add JobQueue behavior or alter `jobs` in this tuple. The test in Step 1 plus the constraint tests in Task 2 are the executable proof that the transcription is complete.

- [ ] **Step 4: Register schema v7 in the migration runner**

In `src/pkb/knowledge/migrations.py`:

```python
from .schema_v7 import KNOWLEDGE_SCHEMA_V7

SCHEMA_VERSION = 7

_MIGRATIONS = {
    1: _MIGRATION_1,
    2: _MIGRATION_2,
    3: _MIGRATION_3,
    4: _MIGRATION_4,
    5: _MIGRATION_5,
    6: _MIGRATION_6,
    7: KNOWLEDGE_SCHEMA_V7,
}
```

- [ ] **Step 5: Extend the existing core table contract**

Add `KNOWLEDGE_TABLES` to `CORE_TABLES` in `tests/test_knowledge_migrations.py`. Keep the existing foreign-key-leading-index test; it must cover every new FK.

- [ ] **Step 6: Run migration tests**

Run: `python -m pytest tests/test_knowledge_migrations.py tests/test_fact_entity_migrations.py -v`

Expected: PASS, including `test_every_foreign_key_column_has_a_leading_index`.

- [ ] **Step 7: Commit**

```powershell
git add src/pkb/knowledge/schema_v7.py src/pkb/knowledge/migrations.py tests/test_knowledge_migrations.py tests/test_fact_entity_migrations.py
git commit -m "feat: add fact entity knowledge schema"
```

## Task 2: Prove Database Constraints and Upgrade Safety

**Files:**
- Modify: `tests/test_fact_entity_migrations.py`
- Modify: `src/pkb/knowledge/migrations.py`

- [ ] **Step 1: Write failing row-level constraint tests**

```python
import pytest


def test_fact_object_is_exactly_one_of_entity_or_value(knowledge_connection):
    entity = insert_accepted_entity(knowledge_connection, "person", "张三")
    with pytest.raises(sqlite3.IntegrityError):
        insert_fact(
            knowledge_connection,
            subject_entity_id=entity,
            object_entity_id=None,
            object_value_json=None,
            object_type="string",
        )


def test_pending_and_rejected_rows_cannot_have_knowledge_status(knowledge_connection):
    with pytest.raises(sqlite3.IntegrityError):
        insert_fact(
            knowledge_connection,
            review_status="pending",
            knowledge_status="active",
        )


def test_symmetric_relation_requires_canonical_order(knowledge_connection):
    left, right = insert_two_facts(knowledge_connection)
    with pytest.raises(sqlite3.IntegrityError):
        insert_relation(
            knowledge_connection,
            left_fact_id=max(left, right),
            right_fact_id=min(left, right),
            relation_type="conflicts_with",
        )
```

Define focused fixture helpers in the test file; every helper must insert the minimum valid parent rows and must not call repository code, so these remain pure schema tests.

- [ ] **Step 2: Run tests to verify at least one constraint fails**

Run: `python -m pytest tests/test_fact_entity_migrations.py -v`

Expected: FAIL until all reviewed CHECK/partial UNIQUE constraints are present.

- [ ] **Step 3: Add trigger contract tests**

```python
def test_fact_publish_requires_accepted_entities_and_valid_evidence(knowledge_connection):
    fact_id = insert_pending_fact_with_pending_subject(knowledge_connection)
    with pytest.raises(sqlite3.IntegrityError, match="subject_entity_not_accepted"):
        knowledge_connection.execute(
            "UPDATE facts SET review_status='accepted', knowledge_status='active' WHERE id=?",
            (fact_id,),
        )


def test_historical_fact_and_audit_event_cannot_be_deleted(knowledge_connection):
    fact_id = insert_pending_fact(knowledge_connection)
    event_id = insert_knowledge_event(knowledge_connection, "fact", fact_id, "created")
    with pytest.raises(sqlite3.IntegrityError):
        knowledge_connection.execute("DELETE FROM facts WHERE id=?", (fact_id,))
    with pytest.raises(sqlite3.IntegrityError):
        knowledge_connection.execute("DELETE FROM knowledge_events WHERE id=?", (event_id,))
```

- [ ] **Step 4: Add v6-to-v7 preservation test**

Create a database through migrations 1–6, insert a document, article derivation, job, and document relation, set `PRAGMA user_version=6`, run `migrate`, then assert all old rows are byte-for-byte logically equal and new tables are empty.

- [ ] **Step 5: Run migration and full regression tests**

Run:

```powershell
python -m pytest tests/test_fact_entity_migrations.py tests/test_knowledge_migrations.py -v
python -m pytest -q
```

Expected: all tests PASS. Existing document/job/derivation contracts remain unchanged.

- [ ] **Step 6: Commit**

```powershell
git add src/pkb/knowledge/migrations.py tests/test_fact_entity_migrations.py
git commit -m "test: enforce knowledge lifecycle constraints"
```

## Task 3: Add Immutable Normalized Text Versions and Grounding

**Files:**
- Create: `src/pkb/knowledge/text_versions.py`
- Modify: `src/pkb/knowledge/repository.py`
- Create: `tests/test_text_versions.py`
- Modify: `tests/test_knowledge_repository.py`

- [ ] **Step 1: Write failing Unicode grounding tests**

```python
import pytest

from pkb.knowledge.text_versions import GroundingError, normalize_plain_text, validate_span


def test_normalized_text_uses_nfkc_and_lf():
    assert normalize_plain_text("Ａ\r\n咖啡") == "A\n咖啡"


def test_offsets_are_unicode_code_points_and_half_open():
    text = "甲😀乙"
    assert validate_span(text, 1, 2, "😀") == "😀"


def test_grounding_rejects_wrong_repeated_excerpt_offset():
    with pytest.raises(GroundingError, match="exact span"):
        validate_span("重复，重复", 0, 2, "重复，")
```

- [ ] **Step 2: Run the tests and verify import failure**

Run: `python -m pytest tests/test_text_versions.py -v`

Expected: FAIL because `text_versions.py` does not exist.

- [ ] **Step 3: Implement strict text helpers**

```python
from __future__ import annotations

import unicodedata


class GroundingError(ValueError):
    pass


def normalize_plain_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _fold_whitespace(value: str) -> str:
    return "".join(value.split())


def validate_span(text: str, start: int, end: int, excerpt: str) -> str:
    if isinstance(start, bool) or isinstance(end, bool) or start < 0 or end <= start:
        raise GroundingError("offsets must form a positive half-open range")
    if end > len(text) or text[start:end] != excerpt:
        raise GroundingError("excerpt does not match exact span")
    if _fold_whitespace(excerpt) not in _fold_whitespace(text):
        raise GroundingError("excerpt was not found in normalized source text")
    return excerpt
```

- [ ] **Step 4: Write failing document snapshot test**

```python
def test_document_upsert_preserves_old_text_version(repository, normalized_document):
    first = repository.upsert_document(
        normalized_document,
        source_hash="s1",
        normalized_hash="n1",
        normalization_version=1,
    )
    changed = replace(normalized_document, plain_content="新正文")
    repository.upsert_document(
        changed,
        source_hash="s2",
        normalized_hash="n2",
        normalization_version=2,
    )
    rows = repository.connection.execute(
        "SELECT normalized_content_hash, plain_content, invalidated_at "
        "FROM document_text_versions WHERE document_id=? ORDER BY id",
        (first.document_id,),
    ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [("n1", normalized_document.plain_content), ("n2", "新正文")]
    assert rows[0][2] is not None and rows[1][2] is None
```

- [ ] **Step 5: Update `KnowledgeRepository.upsert_document` transactionally**

Add a private `_record_text_version` called inside the existing transaction. It inserts the current `(document_id, source_content_hash, normalized_content_hash, normalization_version, plain_content)`, invalidates older current rows only when normalized hash/version changes, and never updates old `plain_content`.

- [ ] **Step 6: Run focused and full tests**

Run:

```powershell
python -m pytest tests/test_text_versions.py tests/test_knowledge_repository.py -v
python -m pytest -q
```

Expected: PASS; unchanged document upserts create no duplicate text version.

- [ ] **Step 7: Commit**

```powershell
git add src/pkb/knowledge/text_versions.py src/pkb/knowledge/repository.py tests/test_text_versions.py tests/test_knowledge_repository.py
git commit -m "feat: preserve normalized text versions"
```

## Task 4: Define Entity Value Objects and Repository

**Files:**
- Create: `src/pkb/knowledge/entity_models.py`
- Create: `src/pkb/knowledge/entity_repository.py`
- Create: `tests/test_entity_repository.py`

- [ ] **Step 1: Write failing entity lifecycle tests**

```python
import pytest

from pkb.knowledge.entity_repository import EntityRepository, InvalidEntityTransition


def test_entity_candidate_must_be_accepted_before_fact_use(entity_repository):
    entity_id = entity_repository.create_candidate("person", "张三", actor="test")
    assert entity_repository.get(entity_id).review_status == "pending"
    entity_repository.accept(entity_id, actor="human:test", reason="identity confirmed")
    assert entity_repository.get(entity_id).review_status == "accepted"


def test_rejected_entity_cannot_be_reaccepted_in_place(entity_repository):
    entity_id = entity_repository.create_candidate("person", "重名", actor="test")
    entity_repository.reject(entity_id, actor="human:test", reason="wrong person")
    with pytest.raises(InvalidEntityTransition):
        entity_repository.accept(entity_id, actor="human:test", reason="changed mind")
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/test_entity_repository.py -v`

Expected: FAIL because entity modules do not exist.

- [ ] **Step 3: Implement frozen models**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Entity:
    id: int
    entity_type: str
    canonical_name: str
    normalized_name: str
    review_status: str
    merged_into_entity_id: int | None


@dataclass(frozen=True)
class Mention:
    id: int
    document_id: int
    entity_id: int | None
    surface_text: str
    start_offset: int
    end_offset: int
    review_status: str
```

Include Alias and explicit error classes `InvalidEntityTransition`, `EntityMergeCycleError`, and `EntityNotFoundError`. Normalize names with NFKC + whitespace collapse + casefold; preserve display name separately.

- [ ] **Step 4: Implement repository lifecycle and audit**

`EntityRepository` owns one SQLite connection with `PRAGMA foreign_keys=ON` and `migrate`. Implement `create_candidate`, `accept`, `reject`, `reopen`, `add_alias`, `add_mention`, `get`, and `resolve_canonical`. Every mutation writes `knowledge_events` in the same transaction.

For `add_mention`, load `document_text_versions.plain_content`, call `validate_span`, and copy its hash/version to the mention row. Only strong identifiers may auto-accept the link; heuristic/LLM links remain pending.

- [ ] **Step 5: Add alias uniqueness and mention version tests**

```python
def test_same_accepted_strong_identifier_cannot_link_two_entities(entity_repository):
    first = accepted_entity(entity_repository, "person", "甲")
    second = accepted_entity(entity_repository, "person", "乙")
    entity_repository.add_alias(first, "email", "A@example.com", strong=True, accept=True)
    with pytest.raises(sqlite3.IntegrityError):
        entity_repository.add_alias(second, "email", "a@example.com", strong=True, accept=True)


def test_mention_binds_exact_text_version(entity_repository, text_version):
    entity_id = accepted_entity(entity_repository, "person", "张三")
    mention = entity_repository.add_mention(
        text_version.id, "张三", 0, 2, "person", entity_id=entity_id,
        method="strong_identifier", accept=True,
    )
    assert mention.entity_id == entity_id
```

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest tests/test_entity_repository.py tests/test_text_versions.py -v && python -m pytest -q`

Expected: PASS.

```powershell
git add src/pkb/knowledge/entity_models.py src/pkb/knowledge/entity_repository.py tests/test_entity_repository.py
git commit -m "feat: add auditable entity repository"
```

## Task 5: Implement Reversible Entity Merge

**Files:**
- Modify: `src/pkb/knowledge/entity_repository.py`
- Modify: `tests/test_entity_repository.py`

- [ ] **Step 1: Write failing merge and undo tests**

```python
def test_merge_resolves_canonical_without_rewriting_fact_ids(entity_repository, fact_repository):
    winner = accepted_entity(entity_repository, "organization", "OpenAI")
    loser = accepted_entity(entity_repository, "organization", "Open AI Inc.")
    subject = accepted_entity(entity_repository, "person", "张三")
    fact_id = accepted_entity_fact(fact_repository, subject, "works_at", loser)

    event_id = entity_repository.merge(loser, winner, actor="human:test", reason="same organization")
    assert entity_repository.resolve_canonical(loser) == winner
    assert fact_repository.get(fact_id).object_entity_id == loser

    entity_repository.undo_merge(event_id, actor="human:test", reason="incorrect merge")
    assert entity_repository.resolve_canonical(loser) == loser
    assert fact_repository.get(fact_id).object_entity_id == loser
```

- [ ] **Step 2: Add cycle rejection test**

```python
def test_merge_rejects_cycle(entity_repository):
    first = accepted_entity(entity_repository, "person", "甲")
    second = accepted_entity(entity_repository, "person", "乙")
    entity_repository.merge(second, first, actor="human:test", reason="candidate")
    with pytest.raises(EntityMergeCycleError):
        entity_repository.merge(first, second, actor="human:test", reason="would cycle")
```

- [ ] **Step 3: Run and verify failure**

Run: `python -m pytest tests/test_entity_repository.py -v`

Expected: FAIL because merge APIs are absent.

- [ ] **Step 4: Implement merge transaction**

Use `BEGIN IMMEDIATE`. Require both entities accepted, resolve winner to its canonical endpoint, run a recursive CTE to prove the winner chain excludes loser, insert `entity_merge_events(action='merge')`, update only `entities.merged_into_entity_id`, and append `knowledge_events`. Never update facts, mentions, aliases, or evidence.

`undo_merge` requires the referenced event to be a non-reversed merge and the current loser pointer to equal the recorded winner. Insert an undo event and clear only that pointer in the same transaction.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_entity_repository.py -v && python -m pytest -q`

Expected: PASS.

```powershell
git add src/pkb/knowledge/entity_repository.py tests/test_entity_repository.py
git commit -m "feat: add reversible entity merges"
```

## Task 6: Define Typed Facts, Identity Keys, and Evidence

**Files:**
- Create: `src/pkb/knowledge/fact_models.py`
- Create: `src/pkb/knowledge/fact_repository.py`
- Create: `tests/test_fact_repository.py`

- [ ] **Step 1: Write failing fact key tests**

```python
from pkb.knowledge.fact_models import EntityObject, ScalarObject, fact_key


def test_fact_key_ignores_observation_and_evidence_metadata():
    first = fact_key(1, "works_at", EntityObject(2), valid_from="2026-01-01", valid_to=None)
    second = fact_key(1, " WORKS_AT ", EntityObject(2), valid_from="2026-01-01", valid_to=None)
    assert first == second
    assert len(first) == 64


def test_typed_scalar_changes_fact_identity():
    assert fact_key(1, "budget", ScalarObject("number", 100), None, None) != fact_key(
        1, "budget", ScalarObject("string", "100"), None, None
    )
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/test_fact_repository.py -v`

Expected: FAIL because fact modules do not exist.

- [ ] **Step 3: Implement strict typed objects and key serialization**

```python
@dataclass(frozen=True)
class EntityObject:
    entity_id: int


@dataclass(frozen=True)
class ScalarObject:
    object_type: str
    value: str | int | float | bool | dict[str, object]
```

Canonicalize the tuple with sorted-key compact JSON, normalized predicate, canonical entity IDs supplied by the repository, normalized ISO time or empty string, then SHA-256 UTF-8 bytes. Add `FACT_KEY_VERSION = 1` to the serialized input; do not persist an unversioned algorithm.

- [ ] **Step 4: Write failing candidate/evidence/publication tests**

```python
def test_fact_publish_requires_valid_evidence_and_accepted_entities(fact_repository, entities, text_version):
    candidate = fact_repository.create_candidate(
        entities.person, "works_at", EntityObject(entities.organization), actor="test"
    )
    with pytest.raises(FactPublicationError, match="valid evidence"):
        fact_repository.accept(candidate.id, actor="human:test", reason="confirmed")

    fact_repository.add_evidence(
        candidate.id, text_version.id, role="supports", start=0, end=10,
        excerpt=text_version.plain_content[:10],
    )
    accepted = fact_repository.accept(candidate.id, actor="human:test", reason="confirmed")
    assert (accepted.review_status, accepted.knowledge_status) == ("accepted", "active")
```

- [ ] **Step 5: Implement repository creation and publication**

Implement `create_candidate`, `add_evidence`, `accept`, `reject`, `reopen`, `get`, and `evidence`. Use `EntityRepository.resolve_canonical` only to calculate the new key; persist original subject/object IDs. Evidence validation loads immutable text version and calls `validate_span`. Publication is one UPDATE inside `BEGIN IMMEDIATE`; database trigger remains the final guard. Every mutation appends `knowledge_events`.

- [ ] **Step 6: Add multi-source evidence test**

```python
def test_second_source_adds_evidence_without_duplicate_fact(fact_repository, two_text_versions, entities):
    first = fact_repository.create_candidate(
        entities.person, "works_at", EntityObject(entities.organization), actor="test"
    )
    add_grounded_evidence(fact_repository, first.id, two_text_versions[0])
    fact_repository.accept(first.id, actor="human:test", reason="confirmed")

    same = fact_repository.find_or_create_candidate(
        entities.person, "works_at", EntityObject(entities.organization), actor="test"
    )
    add_grounded_evidence(fact_repository, same.id, two_text_versions[1])
    assert same.id == first.id
    assert len(fact_repository.evidence(first.id, valid_only=True)) == 2
```

- [ ] **Step 7: Run tests and commit**

Run: `python -m pytest tests/test_fact_repository.py -v && python -m pytest -q`

Expected: PASS.

```powershell
git add src/pkb/knowledge/fact_models.py src/pkb/knowledge/fact_repository.py tests/test_fact_repository.py
git commit -m "feat: add grounded fact repository"
```

## Task 7: Implement Fact Relations and Dispute Transactions

**Files:**
- Modify: `src/pkb/knowledge/fact_models.py`
- Modify: `src/pkb/knowledge/fact_repository.py`
- Create: `tests/test_fact_relation_repository.py`

- [ ] **Step 1: Write failing symmetric conflict test**

```python
def test_valid_pending_conflict_marks_both_facts_disputed(fact_repository, accepted_facts):
    first, second = accepted_facts
    relation = fact_repository.create_relation_candidate(
        second.id,
        first.id,
        "conflicts_with",
        explanation="same subject and period, different object",
        evidence_ids=[first.evidence_ids[0], second.evidence_ids[0]],
        actor="deterministic:conflict-v1",
    )
    assert relation.left_fact_id < relation.right_fact_id
    assert relation.review_status == "pending"
    assert relation.deterministic_validation_status == "passed"
    assert fact_repository.get(first.id).knowledge_status == "disputed"
    assert fact_repository.get(second.id).knowledge_status == "disputed"
```

- [ ] **Step 2: Write failing directional and cycle tests**

```python
def test_refines_preserves_direction(fact_repository, accepted_facts):
    newer, older = accepted_facts
    relation = create_valid_relation(fact_repository, newer, older, "refines")
    assert (relation.left_fact_id, relation.right_fact_id) == (newer.id, older.id)


def test_supersedes_cycle_is_rejected(fact_repository, three_accepted_facts):
    a, b, c = three_accepted_facts
    accept_relation(fact_repository, a, b, "supersedes")
    accept_relation(fact_repository, b, c, "supersedes")
    with pytest.raises(FactRelationCycleError):
        accept_relation(fact_repository, c, a, "supersedes")
```

- [ ] **Step 3: Run and verify failure**

Run: `python -m pytest tests/test_fact_relation_repository.py -v`

Expected: FAIL because relation APIs are absent.

- [ ] **Step 4: Implement relation candidate transaction**

For symmetric types, sort IDs before INSERT. Verify both endpoint Facts are accepted, evidence IDs exist and belong to endpoints, and at least one evidence remains valid. Insert relation + evidence links, set deterministic validation passed, update active endpoints to disputed for `conflicts_with`, and append events in one `BEGIN IMMEDIATE` transaction. Do not accept the relation automatically.

- [ ] **Step 5: Implement human relation decisions**

Add `accept_relation`, `reject_relation`, `supersede`, and `retract`. `accept_relation` enforces passed validation and evidence trigger. `supersede` runs recursive CTE cycle detection before accepting the relation and moves only the right/older Fact to superseded. Rejecting a conflict does not auto-activate endpoints; expose `resolve_dispute(fact_ids, actor, reason)` requiring human actor and proving no other passed conflict remains.

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest tests/test_fact_relation_repository.py tests/test_fact_repository.py -v && python -m pytest -q`

Expected: PASS.

```powershell
git add src/pkb/knowledge/fact_models.py src/pkb/knowledge/fact_repository.py tests/test_fact_relation_repository.py
git commit -m "feat: add auditable fact relations"
```

## Task 8: Implement Evidence Invalidation Propagation

**Files:**
- Modify: `src/pkb/knowledge/fact_repository.py`
- Modify: `src/pkb/knowledge/entity_repository.py`
- Create: `tests/test_fact_invalidation.py`

- [ ] **Step 1: Write failing last-evidence invalidation test**

```python
def test_invalidating_last_evidence_reopens_fact(fact_repository, accepted_fact):
    evidence_id = accepted_fact.evidence_ids[0]
    affected = fact_repository.invalidate_evidence(
        evidence_id, actor="system:normalization-v2", reason="text version stale"
    )
    fact = fact_repository.get(accepted_fact.id)
    assert affected == (accepted_fact.id,)
    assert (fact.review_status, fact.knowledge_status) == ("pending", None)
```

- [ ] **Step 2: Write failing surviving-evidence test**

```python
def test_invalidating_one_of_two_sources_keeps_fact_active(fact_repository, accepted_fact_with_two_sources):
    fact_repository.invalidate_evidence(
        accepted_fact_with_two_sources.evidence_ids[0],
        actor="system:source-update",
        reason="source changed",
    )
    fact = fact_repository.get(accepted_fact_with_two_sources.id)
    assert (fact.review_status, fact.knowledge_status) == ("accepted", "active")
```

- [ ] **Step 3: Run and verify failure**

Run: `python -m pytest tests/test_fact_invalidation.py -v`

Expected: FAIL because invalidation service is absent.

- [ ] **Step 4: Implement bounded invalidation**

`invalidate_text_version(version_id, limit)` selects a bounded set of Mention/Evidence IDs, marks them stale/invalidated, reopens Facts that lose their final valid Evidence, reopens dependent accepted FactRelations, and returns a frozen result containing affected entity/fact/relation IDs for future Synthesis propagation. It must never call a provider or enqueue work in this plan.

Entity `accepted -> pending` first reopens dependent accepted Facts in the same transaction, then clears merge pointer if present, then changes Entity review status. A trigger rejects the transition if any accepted Fact still points to it.

- [ ] **Step 5: Add idempotence test**

```python
def test_repeating_invalidation_is_idempotent(fact_repository, accepted_fact):
    first = fact_repository.invalidate_evidence(
        accepted_fact.evidence_ids[0], actor="system:test", reason="stale"
    )
    second = fact_repository.invalidate_evidence(
        accepted_fact.evidence_ids[0], actor="system:test", reason="stale"
    )
    assert first == (accepted_fact.id,)
    assert second == ()
```

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest tests/test_fact_invalidation.py tests/test_fact_repository.py tests/test_entity_repository.py -v && python -m pytest -q`

Expected: PASS.

```powershell
git add src/pkb/knowledge/fact_repository.py src/pkb/knowledge/entity_repository.py tests/test_fact_invalidation.py
git commit -m "feat: propagate knowledge evidence invalidation"
```

## Task 9: Run the Seven Lifecycle Acceptance Scenarios

**Files:**
- Create: `tests/test_fact_entity_acceptance.py`
- Modify: `docs/second-brain-operations.md`

- [ ] **Step 1: Materialize the seven scenario tests**

Create one test per lifecycle spec section 15.1–15.7 with these exact names: `test_acceptance_normal_extraction_and_publication`, `test_acceptance_conflict_detection_without_auto_adjudication`, `test_acceptance_supersession_preserves_history`, `test_acceptance_entity_merge_and_undo_preserve_fact_ids`, `test_acceptance_evidence_invalidation_and_rebuild`, `test_acceptance_multiple_sources_share_one_fact`, and `test_acceptance_lost_job_lease_cannot_publish`.

The first six use repositories. The lease test composes the existing `JobQueue` with a transaction callback: worker A loses its lease, worker B claims it, and worker A must fail ownership verification before repository publication. Do not add new JobQueue behavior.

- [ ] **Step 2: Run acceptance tests**

Run: `python -m pytest tests/test_fact_entity_acceptance.py -v`

Expected: seven tests PASS.

- [ ] **Step 3: Add operations note**

Add a “Knowledge Layer Status” section stating that schema/repositories exist but Fact extraction, cross-domain job routing, and `pkb think` are not yet enabled. Include the focused verification command and warn operators not to populate knowledge tables manually.

- [ ] **Step 4: Run repository hygiene and full suite**

Run:

```powershell
python -m pytest tests/test_fact_entity_acceptance.py tests/test_repository_hygiene.py tests/test_package_boundaries.py -v
python -m pytest -q
```

Expected: all tests PASS; no raw/frozen fixture changes caused by knowledge tests.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_fact_entity_acceptance.py docs/second-brain-operations.md
git commit -m "test: verify fact entity lifecycle acceptance"
```

## Task 10: Final Schema and Repository Verification

**Files:**
- Modify only if a failing verification exposes a defect in files already listed above.

- [ ] **Step 1: Run schema integrity checks on a fresh database**

```powershell
@'
import sqlite3
from pkb.knowledge.migrations import migrate

connection = sqlite3.connect(":memory:")
migrate(connection)
assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
print("schema-ok")
'@ | python -
```

Expected: `schema-ok`.

- [ ] **Step 2: Run the complete knowledge foundation suite**

```powershell
python -m pytest `
  tests/test_knowledge_migrations.py `
  tests/test_fact_entity_migrations.py `
  tests/test_text_versions.py `
  tests/test_entity_repository.py `
  tests/test_fact_repository.py `
  tests/test_fact_relation_repository.py `
  tests/test_fact_invalidation.py `
  tests/test_fact_entity_acceptance.py -v
```

Expected: PASS.

- [ ] **Step 3: Run the full offline suite**

Run: `python -m pytest -q`

Expected: PASS with no new warnings attributable to the knowledge foundation.

- [ ] **Step 4: Verify no unauthorized scope expansion**

Run:

```powershell
git diff --name-only HEAD~9..HEAD
```

Expected paths are limited to the files listed in this plan. There must be no changes to CLI, MCP, Wiki, provider, prompt, Douyin adapter, or frontend files.

- [ ] **Step 5: Record completion commit if verification required fixes**

Only if Step 1–4 required corrections:

```powershell
git add src/pkb/knowledge tests docs/second-brain-operations.md
git commit -m "fix: close knowledge foundation acceptance gaps"
```

## Completion Gate

- [ ] Schema upgrades from v6 without changing existing document/derivation/job rows.
- [ ] All new FK columns have leading indexes.
- [ ] Illegal review/knowledge combinations fail in SQLite.
- [ ] accepted Fact requires accepted Entity endpoints and valid Evidence.
- [ ] Historical Fact and audit events cannot be deleted through normal SQL paths.
- [ ] Old normalized text snapshots and offsets remain verifiable after document changes.
- [ ] Entity merge and undo never rewrite historical Fact IDs.
- [ ] pending validated conflict marks Facts disputed without accepting or adjudicating the relation.
- [ ] supersedes is acyclic; refines preserves direction.
- [ ] Evidence invalidation is bounded, idempotent, and preserves a Fact with another valid source.
- [ ] Seven lifecycle acceptance scenarios pass.
- [ ] Full offline test suite passes.
- [ ] No JobQueue scope, LLM extraction, Synthesis, CLI, MCP, or UI implementation is included.

## Follow-on Plans

After this completion gate, create separate plans in this order:

1. **Cross-domain JobQueue plan:** nullable document anchor, `scope_hash`, `job_scopes`, migration compatibility, lease tests.
2. **Fact extraction plan:** prompt/schema version, provider pipeline, Candidate validation and bounded review workflow.
3. **Entity linking plan:** strong identifiers, alias matching, heuristic candidates and real-data precision review.
4. **Fact relation plan:** bounded conflict/corroboration/supersession candidate generation over real facts.
5. **Knowledge evaluation plan:** stratified Zhihu/X/Douyin sample and Fact/Entity precision gates.
6. **`pkb think` plan:** retrieval, structured synthesis, claim evidence validation and optional promotion.
7. **Maintenance cycle plan:** stale Evidence rebuild, duplicate Entity candidates, conflict review and Synthesis invalidation.
