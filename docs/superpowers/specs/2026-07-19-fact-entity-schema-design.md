# Fact / Entity 知识层 SQLite Schema 设计

**状态：** 待交叉评审；本文件只定义目标 schema 与约束，不授权创建 migration、修改模型或接入 job pipeline。

**上游规范：** [`2026-07-18-fact-entity-lifecycle-design.md`](2026-07-18-fact-entity-lifecycle-design.md)。上游已冻结的状态机、自动化边界和历史保留语义优先于本设计。

## 1. 目标与设计原则

本设计把 Entity、Alias、Mention、Fact、Evidence 与 FactRelation 落为 SQLite 独立领域表，并与现有 `documents`、`derivations`、`jobs` 建立可审计关联。目标是让数据库阻止行内非法状态，让事务和触发器保证跨行不变量，让服务层处理图环、Unicode 验证、权限和失效传播等 SQLite CHECK 无法可靠表达的规则。

设计原则：

1. Raw 仍是原始证据，`documents` 仍是当前标准化投影。
2. 知识表不复用 `derivations.payload_json` 充当查询模型；`derivations` 保存运行审计，领域表保存受约束的当前及历史知识。
3. 历史记录不物理删除；当前可见性由 review、knowledge 与 validity 状态共同决定。
4. Entity 合并只写 canonical 指针和事件，不改写历史 Fact 外键。
5. offset 必须绑定不可变标准化文本快照，不能只绑定会更新的 `documents.plain_content`。
6. 所有普通任务均有界、可恢复、幂等；本文件只设计未来需要的 scope 结构，不修改现有 JobQueue。

## 2. 表清单

### 2.1 六张核心领域表

| 表 | 作用 |
|---|---|
| `entities` | Entity 候选、审核状态和可撤销 canonical 合并指针。 |
| `entity_aliases` | Entity 的名称与强标识，保留来源和审核信息。 |
| `entity_mentions` | document 文本版本中的 Entity 文本片段和链接结果。 |
| `facts` | subject–predicate–object 事实及双状态生命周期。 |
| `fact_evidence` | Fact 到不可变标准化正文片段的多来源证据。 |
| `fact_relations` | Fact 间冲突、佐证、取代和细化关系。 |

### 2.2 为满足审计与跨域不变量增加的辅助表

| 表或视图 | 作用 |
|---|---|
| `document_text_versions` | 保存不可变标准化正文快照，使旧 excerpt/offset 可验证。 |
| `fact_relation_evidence` | 把 FactRelation 关联到一条或多条已验证 Fact Evidence。 |
| `entity_merge_events` | 记录 Entity 合并及撤销，保证人工操作可审计。 |
| `knowledge_events` | 记录候选、审核、状态、失效与处置的 append-only 审计事件。 |
| `derivation_scopes` | 描述跨 document derivation 的全部输入范围。 |
| `job_scopes` | 描述 document、entity、fact、relation 或 synthesis 任务范围。 |
| `canonical_entities` | 递归解析 `merged_into_entity_id` 的查询视图。 |

`document_text_versions` 不是第二份 Raw；它是由 Raw 可重建、按 hash/version 定位的 Derived 输入快照。没有该表，当前 `documents` 更新后无法验证历史 offset，生命周期规范的历史审计要求无法实现。

## 3. 字段约定

- ID 使用现有 SQLite 风格的 `INTEGER PRIMARY KEY`；外部稳定 ID 如需暴露，可在接口层另做编码。
- 时间统一为 UTC ISO-8601 TEXT，沿用现有数据库习惯；应用层拒绝无时区输入。
- 布尔值使用 `INTEGER NOT NULL DEFAULT 0 CHECK(value IN (0,1))`。
- JSON 字段必须通过 `json_valid`；规范化 JSON 由应用层以 key 排序、紧凑分隔符、UTF-8 输出生成。
- `review_status` 仅允许 `pending|accepted|rejected`。
- Fact/FactRelation 的 `knowledge_status` 仅允许 `active|disputed|superseded|retracted` 或 NULL，并使用同一合法组合 CHECK。
- stale 不是 review 或 knowledge 状态；它是版本有效性，用 `invalidated_at IS NULL` 表示当前有效。

## 4. 目标 DDL

以下 DDL 是设计稿，不得直接复制进 migration；正式迁移必须另行计划、在临时数据库验证并遵循现有 `PRAGMA user_version` 机制。

### 4.1 不可变标准化文本版本

```sql
CREATE TABLE document_text_versions (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    source_content_hash TEXT NOT NULL,
    normalized_content_hash TEXT NOT NULL,
    normalization_version INTEGER NOT NULL CHECK(normalization_version > 0),
    plain_content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    UNIQUE(document_id, normalized_content_hash, normalization_version),
    CHECK((invalidated_at IS NULL) = (invalidation_reason IS NULL))
);
```

`invalidated_at` 表示该版本不再是当前抽取输入，不表示快照被删除或内容不可信。旧 Evidence 仍通过 FK 指向该快照进行历史验证。

### 4.2 `entities`

```sql
CREATE TABLE entities (
    id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK(entity_type IN (
        'person','organization','project','concept','decision','event','claim'
    )),
    canonical_name TEXT NOT NULL CHECK(length(trim(canonical_name)) > 0),
    normalized_name TEXT NOT NULL CHECK(length(normalized_name) > 0),
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(review_status IN ('pending','accepted','rejected')),
    merged_into_entity_id INTEGER REFERENCES entities(id) ON DELETE RESTRICT,
    creation_derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(merged_into_entity_id IS NULL OR merged_into_entity_id <> id),
    CHECK(review_status = 'accepted' OR merged_into_entity_id IS NULL)
);
```

`reviewed_at/by/reason` 保存最近一次人工判断的摘要，不用 CHECK 把 pending 强制为 NULL；因为 accepted -> pending 重新审核必须保留先前判断。完整历史由 `knowledge_events` 保存。accepted/rejected 的人工操作仍由服务层要求 actor、reason 与时间，确定性自动接受则写明确的规则 actor。

Entity 不增加 `knowledge_status` 或 `active_status`。运行状态由 `review_status` 与 `merged_into_entity_id` 派生：accepted 且无合并指针为 canonical；accepted 且有指针为 merged；pending/rejected 不进入当前知识视图。这样避免 active 与 merged 两个字段漂移。

### 4.3 `entity_aliases`

```sql
CREATE TABLE entity_aliases (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE RESTRICT,
    alias_kind TEXT NOT NULL CHECK(alias_kind IN (
        'name','email','url','platform_id','organization_code','other'
    )),
    alias_value TEXT NOT NULL CHECK(length(trim(alias_value)) > 0),
    normalized_value TEXT NOT NULL CHECK(length(normalized_value) > 0),
    namespace TEXT NOT NULL DEFAULT '',
    is_strong_identifier INTEGER NOT NULL DEFAULT 0
        CHECK(is_strong_identifier IN (0,1)),
    source_document_id INTEGER REFERENCES documents(id) ON DELETE RESTRICT,
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(review_status IN ('pending','accepted','rejected')),
    confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
    derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK((invalidated_at IS NULL) = (invalidation_reason IS NULL))
);

CREATE UNIQUE INDEX uq_entity_alias_candidate
ON entity_aliases(entity_id, alias_kind, namespace, normalized_value)
WHERE review_status IN ('pending','accepted') AND invalidated_at IS NULL;

CREATE UNIQUE INDEX uq_entity_alias_strong_global
ON entity_aliases(alias_kind, namespace, normalized_value)
WHERE is_strong_identifier = 1
  AND review_status = 'accepted'
  AND invalidated_at IS NULL;
```

`namespace` 区分不同平台的 platform ID，也用于限制 email/url 等强标识的唯一域。弱名称允许跨 Entity 重复，不能自动合并。

### 4.4 `entity_mentions`

```sql
CREATE TABLE entity_mentions (
    id INTEGER PRIMARY KEY,
    document_text_version_id INTEGER NOT NULL
        REFERENCES document_text_versions(id) ON DELETE RESTRICT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    normalized_content_hash TEXT NOT NULL,
    normalization_version INTEGER NOT NULL CHECK(normalization_version > 0),
    entity_id INTEGER REFERENCES entities(id) ON DELETE RESTRICT,
    surface_text TEXT NOT NULL CHECK(length(surface_text) > 0),
    start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK(end_offset > start_offset),
    mention_type TEXT NOT NULL CHECK(mention_type IN (
        'person','organization','project','concept','decision','event','claim','unknown'
    )),
    linking_method TEXT NOT NULL CHECK(linking_method IN (
        'unresolved','strong_identifier','exact_alias','heuristic','llm','human'
    )),
    linking_confidence REAL
        CHECK(linking_confidence IS NULL OR linking_confidence BETWEEN 0.0 AND 1.0),
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(review_status IN ('pending','accepted','rejected')),
    derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(document_id, normalized_content_hash, normalization_version)
        REFERENCES document_text_versions(
            document_id, normalized_content_hash, normalization_version
        ) ON DELETE RESTRICT,
    CHECK(review_status <> 'accepted' OR entity_id IS NOT NULL),
    CHECK(linking_method <> 'unresolved' OR entity_id IS NULL),
    CHECK((invalidated_at IS NULL) = (invalidation_reason IS NULL))
);

CREATE UNIQUE INDEX uq_entity_mention_current_span
ON entity_mentions(
    document_text_version_id, start_offset, end_offset, surface_text
)
WHERE review_status IN ('pending','accepted') AND invalidated_at IS NULL;
```

同时保存 `document_text_version_id` 与三元组是有意冗余：前者简化读取，复合 FK 确保 hash/version 与 document 一致。应用层还必须验证该 version row 的 `id` 与复合键指向同一行。

### 4.5 `facts`

```sql
CREATE TABLE facts (
    id INTEGER PRIMARY KEY,
    fact_key TEXT NOT NULL CHECK(length(fact_key) = 64),
    subject_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE RESTRICT,
    predicate TEXT NOT NULL CHECK(length(trim(predicate)) > 0),
    object_entity_id INTEGER REFERENCES entities(id) ON DELETE RESTRICT,
    object_value_json TEXT,
    object_type TEXT NOT NULL CHECK(object_type IN (
        'entity','string','number','boolean','date','datetime','money','quantity'
    )),
    object_normalized_text TEXT NOT NULL CHECK(length(object_normalized_text) > 0),
    valid_from TEXT,
    valid_to TEXT,
    observed_at TEXT,
    confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(review_status IN ('pending','accepted','rejected')),
    knowledge_status TEXT
        CHECK(knowledge_status IN ('active','disputed','superseded','retracted')),
    creation_derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK((object_entity_id IS NOT NULL) <> (object_value_json IS NOT NULL)),
    CHECK((object_type = 'entity') = (object_entity_id IS NOT NULL)),
    CHECK(object_value_json IS NULL OR json_valid(object_value_json)),
    CHECK(valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to),
    CHECK(
        (review_status IN ('pending','rejected') AND knowledge_status IS NULL)
        OR
        (review_status = 'accepted' AND knowledge_status IS NOT NULL)
    )
);

CREATE UNIQUE INDEX uq_fact_live_key
ON facts(fact_key)
WHERE review_status IN ('pending','accepted');
```

多个 rejected 历史候选可共享 `fact_key`，但同一工作身份最多存在一个 pending/accepted Fact。新来源佐证应增加 Evidence，而不是再建 active Fact。

### 4.6 `fact_evidence`

```sql
CREATE TABLE fact_evidence (
    id INTEGER PRIMARY KEY,
    fact_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE RESTRICT,
    document_text_version_id INTEGER NOT NULL
        REFERENCES document_text_versions(id) ON DELETE RESTRICT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    normalized_content_hash TEXT NOT NULL,
    normalization_version INTEGER NOT NULL CHECK(normalization_version > 0),
    evidence_role TEXT NOT NULL CHECK(evidence_role IN (
        'supports','contradicts','context'
    )),
    excerpt TEXT NOT NULL CHECK(length(excerpt) > 0),
    start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK(end_offset > start_offset),
    observed_at TEXT,
    derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
    validation_status TEXT NOT NULL DEFAULT 'valid'
        CHECK(validation_status IN ('valid','invalid','stale')),
    invalidated_at TEXT,
    invalidation_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(document_id, normalized_content_hash, normalization_version)
        REFERENCES document_text_versions(
            document_id, normalized_content_hash, normalization_version
        ) ON DELETE RESTRICT,
    CHECK((validation_status = 'valid' AND invalidated_at IS NULL AND invalidation_reason IS NULL)
       OR (validation_status IN ('invalid','stale')
           AND invalidated_at IS NOT NULL AND invalidation_reason IS NOT NULL))
);

CREATE UNIQUE INDEX uq_fact_evidence_span
ON fact_evidence(fact_id, document_text_version_id, start_offset, end_offset, evidence_role)
WHERE validation_status = 'valid';
```

### 4.7 `fact_relations`

```sql
CREATE TABLE fact_relations (
    id INTEGER PRIMARY KEY,
    left_fact_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE RESTRICT,
    right_fact_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE RESTRICT,
    relation_type TEXT NOT NULL CHECK(relation_type IN (
        'conflicts_with','corroborates','supersedes','refines'
    )),
    confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
    explanation TEXT NOT NULL CHECK(length(trim(explanation)) > 0),
    deterministic_validation_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(deterministic_validation_status IN ('pending','passed','failed')),
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(review_status IN ('pending','accepted','rejected')),
    knowledge_status TEXT
        CHECK(knowledge_status IN ('active','disputed','superseded','retracted')),
    derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(left_fact_id <> right_fact_id),
    CHECK(relation_type NOT IN ('conflicts_with','corroborates')
       OR left_fact_id < right_fact_id),
    CHECK(
        (review_status IN ('pending','rejected') AND knowledge_status IS NULL)
        OR
        (review_status = 'accepted' AND knowledge_status IS NOT NULL)
    ),
    CHECK(review_status <> 'accepted' OR deterministic_validation_status = 'passed')
);

CREATE UNIQUE INDEX uq_fact_relation_symmetric_live
ON fact_relations(left_fact_id, right_fact_id, relation_type)
WHERE relation_type IN ('conflicts_with','corroborates')
  AND review_status IN ('pending','accepted');

CREATE UNIQUE INDEX uq_fact_relation_directed_live
ON fact_relations(left_fact_id, right_fact_id, relation_type)
WHERE relation_type IN ('supersedes','refines')
  AND review_status IN ('pending','accepted');
```

对 `supersedes` 和 `refines`，`left_fact_id` 表示新事实/更精确事实，`right_fact_id` 表示被取代/被细化事实。pending `conflicts_with` 只有在 `deterministic_validation_status='passed'` 且至少存在一条有效 relation evidence 时，服务层才能把端点 Fact 标为 disputed。

### 4.8 `fact_relation_evidence`

```sql
CREATE TABLE fact_relation_evidence (
    fact_relation_id INTEGER NOT NULL REFERENCES fact_relations(id) ON DELETE RESTRICT,
    fact_evidence_id INTEGER NOT NULL REFERENCES fact_evidence(id) ON DELETE RESTRICT,
    evidence_role TEXT NOT NULL CHECK(evidence_role IN (
        'supports_relation','challenges_relation','context'
    )),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(fact_relation_id, fact_evidence_id, evidence_role)
);
```

关系证据复用已经定位并验证的 Fact Evidence，不复制 excerpt。服务层必须确认关联 Evidence 属于关系任一端点 Fact，除非 role 是明确的外部 context。

### 4.9 `entity_merge_events`

```sql
CREATE TABLE entity_merge_events (
    id INTEGER PRIMARY KEY,
    loser_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE RESTRICT,
    winner_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK(action IN ('merge','undo')),
    reverses_event_id INTEGER REFERENCES entity_merge_events(id) ON DELETE RESTRICT,
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    actor TEXT NOT NULL CHECK(length(trim(actor)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(loser_entity_id <> winner_entity_id),
    CHECK((action = 'merge' AND reverses_event_id IS NULL)
       OR (action = 'undo' AND reverses_event_id IS NOT NULL))
);
```

merge/undo 事件与 `entities.merged_into_entity_id` 的更新必须在同一 `BEGIN IMMEDIATE` 事务内完成。undo 不删除原 merge 事件。

### 4.10 `knowledge_events`

```sql
CREATE TABLE knowledge_events (
    id INTEGER PRIMARY KEY,
    object_type TEXT NOT NULL CHECK(object_type IN (
        'entity','entity_alias','entity_mention','fact','fact_evidence',
        'fact_relation','entity_merge'
    )),
    object_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'created','validated','accepted','rejected','reopened','disputed',
        'superseded','retracted','invalidated','rebuilt','merged','merge_undone'
    )),
    actor_type TEXT NOT NULL CHECK(actor_type IN (
        'deterministic_rule','llm_candidate','human','maintenance_task','system'
    )),
    actor_id TEXT,
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    derivation_id TEXT REFERENCES derivations(id) ON DELETE SET NULL,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    previous_state_json TEXT,
    new_state_json TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(previous_state_json IS NULL OR json_valid(previous_state_json)),
    CHECK(new_state_json IS NULL OR json_valid(new_state_json)),
    CHECK(details_json IS NULL OR json_valid(details_json))
);

CREATE TRIGGER knowledge_events_no_update
BEFORE UPDATE ON knowledge_events
BEGIN
    SELECT RAISE(ABORT, 'knowledge_events_are_append_only');
END;

CREATE TRIGGER knowledge_events_no_delete
BEFORE DELETE ON knowledge_events
BEGIN
    SELECT RAISE(ABORT, 'knowledge_events_are_append_only');
END;

CREATE TRIGGER facts_no_delete
BEFORE DELETE ON facts
BEGIN
    SELECT RAISE(ABORT, 'historical_facts_cannot_be_deleted');
END;
```

`object_type + object_id` 是受控多态引用，SQLite 无法用一个 FK 指向多张表；写事件的领域服务必须在同一事务验证对象存在。隐私擦除若未来获批，需要专门的受审计迁移通道，而不是关闭普通服务中的 trigger。

## 5. Entity canonical 查询设计

Entity 不使用独立 active/merged 字段。所有读取通过递归 CTE 解析：

```sql
CREATE VIEW canonical_entities AS
WITH RECURSIVE chain(origin_id, current_id, depth, path) AS (
    SELECT id, id, 0, printf('/%d/', id) FROM entities
    UNION ALL
    SELECT chain.origin_id, entity.merged_into_entity_id, chain.depth + 1,
           chain.path || printf('%d/', entity.merged_into_entity_id)
    FROM chain
    JOIN entities AS entity ON entity.id = chain.current_id
    WHERE entity.merged_into_entity_id IS NOT NULL
      AND chain.depth < 64
      AND instr(chain.path, printf('/%d/', entity.merged_into_entity_id)) = 0
)
SELECT chain.origin_id AS entity_id,
       chain.current_id AS canonical_entity_id,
       chain.depth
FROM chain
JOIN entities AS entity ON entity.id = chain.current_id
WHERE entity.merged_into_entity_id IS NULL;
```

写入 merge 前，服务层必须递归检查：winner 可解析、winner 链不包含 loser、链深小于上限且两端均 accepted。视图的 depth 上限是防御，不替代写入时无环验证。查询若无法得到唯一 canonical 行必须失败并报告完整性错误，不能回退到原 ID。

## 6. Unicode、excerpt 与 offset 规则

### 6.1 文本基准

offset 针对 `document_text_versions.plain_content` 的原样字符串，而不是 Raw HTML、UTF-8 byte 流或去空白后的字符串。标准化 pipeline 写入快照前必须：

1. 将换行统一为 LF；
2. 第一版明确使用 Unicode **NFKC** 规范化，并在 `normalization_version` 中记录该规则；
3. 保留最终 `plain_content`，之后不得原地修改。

NFKC 只适用于引入该规则的新 normalization version，不能追溯改写旧快照。旧版本如果使用其他规则或未做 Unicode 规范化，仍按其保存的 `plain_content` 和版本号解释；迁移会创建新 version row。

本阶段选择 **Unicode code point、零基、半开区间 `[start_offset, end_offset)`**。Python 对 `str` 的切片与该语义一致；不使用 UTF-8 byte offset，也不使用 UTF-16 code unit。组合字符如何组成用户感知 grapheme 不影响可重建切片，因为 offset 绑定已规范化 code point 序列。

### 6.2 确定性验证

Mention 必须满足：

```text
plain_content[start_offset:end_offset] == surface_text
```

Evidence 必须满足：

```text
plain_content[start_offset:end_offset] == excerpt
normalize_whitespace(excerpt) in normalize_whitespace(plain_content)
```

第二条沿用现有 validator 的最低 grounding 规则；第一条新增精确定位保证。任何一条失败都不得写成 `validation_status='valid'`。SQLite 不能按 Python Unicode code point 语义可靠执行这项验证，因此由严格 value object validator 在同一写事务前执行；数据库负责非负、有序 offset、版本 FK 与状态 CHECK。

重复 excerpt 通过 offset 区分。normalization_version 变化必须建立新 `document_text_versions` 行并重建 Mention/Evidence；旧行转 stale，不覆盖。

## 7. Fact 身份键工作定义

第一版 `fact_key` 为以下 canonical tuple 的 SHA-256 小写十六进制：

```text
(
  canonical(subject_entity_id),
  normalize_predicate(predicate),
  object_kind,
  canonical(object_entity_id) 或 canonical_json(object_value_json),
  normalize_time(valid_from) 或 "",
  normalize_time(valid_to) 或 ""
)
```

序列化使用长度前缀或 canonical JSON，禁止仅以分隔符拼接。Entity ID 在计算时解析 canonical chain；合并或撤销会改变计算视图，因此服务层查询同一性时还必须尝试原 ID 与 canonical ID，不能批量重写历史 `fact_key`。新写入使用当时 canonical 结果，并在合并事件后运行有界重复候选报告。

`observed_at`、confidence、Evidence、derivation/model 不进入身份键；它们描述观察和来源，不改变事实主张。对于没有有效时间的 Fact，空时间属于身份的一部分。

**该定义是可运行的第一版，待真实数据验证后可能调整，不是最终冻结。** 重点评估 predicate 同义词、区间边界、数值单位、Entity 合并后的重复候选，以及同一持续事实被多次观察的行为。调整身份算法必须版本化，不能静默重算并覆盖旧 key。

## 8. 避免 `accepted + NULL` 中间态

`facts` 与 `fact_relations` 的行内 CHECK 直接拒绝 `accepted + NULL`。发布采用单条 UPDATE 或同一事务：

```sql
BEGIN IMMEDIATE;
UPDATE facts
SET review_status = 'accepted',
    knowledge_status = 'active',
    reviewed_at = :now,
    reviewed_by = :actor,
    review_reason = :reason,
    updated_at = :now
WHERE id = :fact_id AND review_status = 'pending';
COMMIT;
```

SQLite 读者看不到未提交中间态；CHECK 保证即使应用错误地分两条语句，第一条非法 UPDATE 也失败。跨表前置条件通过 BEFORE UPDATE trigger 加服务事务双重保证：

```sql
CREATE TRIGGER facts_before_publish
BEFORE UPDATE OF review_status, knowledge_status ON facts
WHEN NEW.review_status = 'accepted'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM entities
        WHERE id = NEW.subject_entity_id AND review_status = 'accepted'
    ) THEN RAISE(ABORT, 'subject_entity_not_accepted') END;
    SELECT CASE WHEN NEW.object_entity_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM entities
        WHERE id = NEW.object_entity_id AND review_status = 'accepted'
    ) THEN RAISE(ABORT, 'object_entity_not_accepted') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM fact_evidence
        WHERE fact_id = NEW.id AND validation_status = 'valid'
    ) THEN RAISE(ABORT, 'fact_has_no_valid_evidence') END;
END;

CREATE TRIGGER fact_relations_before_publish
BEFORE UPDATE OF review_status, knowledge_status ON fact_relations
WHEN NEW.review_status = 'accepted'
BEGIN
    SELECT CASE WHEN NEW.deterministic_validation_status <> 'passed'
        THEN RAISE(ABORT, 'fact_relation_not_validated') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM fact_relation_evidence AS relation_evidence
        JOIN fact_evidence AS evidence
          ON evidence.id = relation_evidence.fact_evidence_id
        WHERE relation_evidence.fact_relation_id = NEW.id
          AND evidence.validation_status = 'valid'
    ) THEN RAISE(ABORT, 'fact_relation_has_no_valid_evidence') END;
END;
```

INSERT 若未来允许直接 accepted，也必须有等价 BEFORE INSERT trigger；推荐服务层永远先插 pending，再显式发布。状态转移合法性还需 BEFORE UPDATE trigger 比较 OLD/NEW，只允许生命周期规范冻结的边。

Entity 从 accepted 退回 pending 时，服务必须在同一事务先把以它为 subject/object 的 accepted Fact 转为 pending + NULL，或拒绝该 Entity 转移；不得留下 active Fact 指向 pending Entity。该跨表传播由服务事务与 BEFORE UPDATE trigger 共同实现，顺序是先处置依赖 Fact，再改变 Entity。

## 9. 冲突候选触发 disputed

服务使用一个 `BEGIN IMMEDIATE` 事务完成：

1. 插入 pending `conflicts_with` Candidate；
2. 插入 `fact_relation_evidence`；
3. 确定性校验端点、excerpt、证据归属和规范方向；
4. 更新 `deterministic_validation_status='passed'`；
5. 将两个 accepted + active Fact 更新为 accepted + disputed；已 disputed 的保持不变；
6. 写审计事件后提交。

关系保持 `pending + NULL`，直到人工接受或拒绝。若候选后来 rejected，Fact 不自动恢复 active；冲突消失属于人工裁决，必须明确检查是否还有其他 passed `conflicts_with` Candidate。

## 10. 索引设计

```sql
CREATE INDEX idx_text_versions_document_current
ON document_text_versions(document_id, invalidated_at, normalization_version);

CREATE INDEX idx_entities_review_name
ON entities(review_status, entity_type, normalized_name);
CREATE INDEX idx_entities_merged_into
ON entities(merged_into_entity_id) WHERE merged_into_entity_id IS NOT NULL;

CREATE INDEX idx_alias_entity_review
ON entity_aliases(entity_id, review_status, invalidated_at);
CREATE INDEX idx_alias_lookup
ON entity_aliases(alias_kind, namespace, normalized_value, review_status, invalidated_at);

CREATE INDEX idx_mentions_document
ON entity_mentions(document_id, invalidated_at, start_offset);
CREATE INDEX idx_mentions_entity
ON entity_mentions(entity_id, review_status, invalidated_at);
CREATE INDEX idx_mentions_derivation
ON entity_mentions(derivation_id);

CREATE INDEX idx_facts_subject_status
ON facts(subject_entity_id, review_status, knowledge_status, predicate);
CREATE INDEX idx_facts_object_entity_status
ON facts(object_entity_id, review_status, knowledge_status, predicate)
WHERE object_entity_id IS NOT NULL;
CREATE INDEX idx_facts_status
ON facts(review_status, knowledge_status, updated_at);
CREATE INDEX idx_facts_predicate_time
ON facts(predicate, valid_from, valid_to);
CREATE INDEX idx_facts_derivation
ON facts(creation_derivation_id);

CREATE INDEX idx_fact_evidence_fact_valid
ON fact_evidence(fact_id, validation_status, document_id);
CREATE INDEX idx_fact_evidence_document
ON fact_evidence(document_id, validation_status, start_offset);
CREATE INDEX idx_fact_evidence_derivation
ON fact_evidence(derivation_id);

CREATE INDEX idx_fact_relations_left
ON fact_relations(left_fact_id, relation_type, review_status, knowledge_status);
CREATE INDEX idx_fact_relations_right
ON fact_relations(right_fact_id, relation_type, review_status, knowledge_status);
CREATE INDEX idx_fact_relations_validation
ON fact_relations(relation_type, deterministic_validation_status, review_status);
CREATE INDEX idx_fact_relations_derivation
ON fact_relations(derivation_id);

CREATE INDEX idx_relation_evidence_evidence
ON fact_relation_evidence(fact_evidence_id);
CREATE INDEX idx_merge_events_loser
ON entity_merge_events(loser_entity_id, created_at);
CREATE INDEX idx_merge_events_winner
ON entity_merge_events(winner_entity_id, created_at);
CREATE INDEX idx_knowledge_events_object
ON knowledge_events(object_type, object_id, created_at);
CREATE INDEX idx_knowledge_events_derivation
ON knowledge_events(derivation_id) WHERE derivation_id IS NOT NULL;
CREATE INDEX idx_knowledge_events_job
ON knowledge_events(job_id) WHERE job_id IS NOT NULL;
```

核心路径分别覆盖：按 subject/object 查当前 Fact、按 document 查 Mention/Evidence、按 Fact 查有效 Evidence、按双状态过滤、按两端遍历关系、按 alias 查 Entity，以及按合并指针解析 canonical。

## 11. 与现有 `derivations` 的关系

六张核心表是独立领域表，不向现有 `derivations` 增加 Entity/Fact 专用列。原因：

- `derivations.payload_json` 是不可变运行产物与审计证据，适合记录模型完整响应；领域表需要 FK、CHECK、索引、多来源 Evidence 和可审核状态。
- 一个 fact-extraction derivation 可产生多个 Mention、Entity Candidate、Fact 与 Evidence；一个 Fact 又可汇聚多个 derivation 和 document 的 Evidence，无法等同于单个 derivation 行。
- prompt/model 变化产生新 derivation，但不应删除已审核领域记录；服务层按 input hash 和审核策略 reconcile。
- 领域行保留 `derivation_id` 或 `creation_derivation_id` 追溯创建运行；人工创建允许 NULL，但必须另有 actor 审计。

跨 document derivation 增加设计表：

```sql
CREATE TABLE derivation_scopes (
    derivation_id TEXT NOT NULL REFERENCES derivations(id) ON DELETE CASCADE,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('document','entity','fact','fact_relation')),
    scope_id TEXT NOT NULL,
    scope_role TEXT NOT NULL CHECK(scope_role IN ('anchor','input','output','context')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(derivation_id, scope_type, scope_id, scope_role)
);
```

现有 `derivations.document_id` 暂时保留为审计 anchor：document 任务使用自身；FactRelation 选择参与 Evidence 的最小 `document_id` 作为稳定 anchor，并在 `derivation_scopes` 列全体输入。Entity merge 是人工事件，不强行创建 derivation。未来 Synthesis 应使用独立 `synthesis_runs`，不塞入本阶段六张表。

## 12. Job scope 的具体解法

当前 `jobs.document_id INTEGER NOT NULL` 无法自然表达 Entity merge、FactRelation 和 Synthesis。目标 schema 采用“nullable anchor + 强类型 scope rows”：正式迁移时重建 `jobs`，把 `document_id` 改为 nullable，并增加：

```sql
CREATE TABLE job_scopes (
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    scope_type TEXT NOT NULL CHECK(scope_type IN (
        'document','entity','fact','fact_relation','synthesis'
    )),
    scope_id TEXT NOT NULL,
    scope_role TEXT NOT NULL CHECK(scope_role IN ('anchor','input','output','context')),
    ordinal INTEGER NOT NULL DEFAULT 0 CHECK(ordinal >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(job_id, scope_type, scope_id, scope_role)
);

CREATE UNIQUE INDEX uq_job_scope_ordinal
ON job_scopes(job_id, scope_role, ordinal);
```

并增加应用/trigger 约束：每个 job 至少一个 `anchor`；document-scoped job 的 `jobs.document_id` 与 document anchor 一致；跨域 job 的 `document_id` 可为空；claim 仍按 job row 执行，scope 不参与 lease 所有权。

原 `UNIQUE(job_type, document_id, input_hash, pipeline_version)` 遇到 NULL 不能保证幂等，因此目标 `jobs` 增加 `scope_hash TEXT NOT NULL`，唯一键改为：

```text
UNIQUE(job_type, scope_hash, input_hash, pipeline_version)
```

`scope_hash` 是排序后的 `(scope_type, scope_id, scope_role)` canonical JSON 的 SHA-256。`job_scopes` 与 job 插入必须同一事务；JobQueue 扩展属于路线图第 4 步，不在本任务实现。

Entity merge 本身必须人工执行，因此不需要 merge job；后台只能创建 merge Candidate，其 scope 使用两个 entity input。FactRelation scope 使用两个 fact input；Synthesis 使用 synthesis anchor 加 document/fact context。

## 13. 不变量实现映射

生命周期规范当前第 12 节共有 24 条不变量；任务提示中所称“22 条”是加入 Entity 发布前置条件和 `refines` 方向性之前的计数。本设计覆盖修订后的全部 24 条。

| # | 不变量摘要 | 数据库保证 | 应用层/事务保证 |
|---:|---|---|---|
| 1 | 历史 Fact 不物理删除 | Fact FK 均 `ON DELETE RESTRICT`；正式迁移增加 `facts_no_delete` trigger | 无普通 delete API；隐私擦除另立流程。 |
| 2 | 发布 Fact 至少一条有效 Evidence | `facts_before_publish` trigger 查询 valid Evidence | Evidence 与发布在同一事务，失效最后一条 Evidence 时先转 Fact pending。 |
| 3 | excerpt 存在于绑定正文 | version 复合 FK、offset CHECK | Python code-point 精确切片 + 现有 whitespace grounding validator。 |
| 4 | Mention 绑定 hash/version | NOT NULL + 复合 FK | 校验 version ID 与复合键是同一行。 |
| 5 | Fact object 恰好二选一 | XOR CHECK + object_type CHECK | canonical JSON/type validator。 |
| 6 | subject 必须是 Entity | NOT NULL FK | 不接受裸 subject 字符串。 |
| 7 | 发布 Fact 引用 Entity 均 accepted | `facts_before_publish` trigger | 发布事务先发布 Entity，后发布 Fact。 |
| 8 | value JSON 受控 | `json_valid` + object_type CHECK | 每类 object value 严格 schema validator。 |
| 9 | conflicts 端点排序 | relation CHECK | 写入前排序。 |
| 10 | 对称关系不重复/不自连 | partial UNIQUE + self CHECK | canonical relation builder。 |
| 11 | supersedes 有向无环 | 有向唯一索引；SQLite CHECK 不能查图 | BEGIN IMMEDIATE 内 recursive CTE 检测环后写入。 |
| 12 | refines 有向 | directed partial UNIQUE，不排序 CHECK | builder 保留输入方向并验证语义。 |
| 13 | merge 链无环且唯一 canonical | self CHECK、单一 merged pointer | 写事务 recursive CTE 检测环；读取视图必须恰好一行。 |
| 14 | merge 不改写历史引用 | Entity FK `ON DELETE RESTRICT`；无级联更新 | merge service 只写 pointer/event；所有读路径 canonical resolve。 |
| 15 | 状态组合合法 | facts/relations CHECK | Entity 不使用 knowledge status。 |
| 16 | pending->accepted 与 active 原子 | CHECK 拒绝 accepted+NULL | 单 UPDATE、BEGIN IMMEDIATE、状态转移 trigger。 |
| 17 | accepted->pending 同时清空 knowledge | CHECK 拒绝 pending+非空 | 单 UPDATE + 当前投影在同一事务失效。 |
| 18 | rejected 不进默认查询 | 状态 CHECK；可建 current views | repository 所有默认路径显式 accepted 过滤。 |
| 19 | disputed Think 必须透出 | 状态和 conflict relation 可查询 | Synthesis validator 拒绝把 disputed claim 标 supported。 |
| 20 | retracted/superseded 默认过滤 | 状态索引支持 | 当前查询只取 active/disputed；轨迹查询显式扩大。 |
| 21 | Synthesis claim 有具体证据 | 本阶段不建 Synthesis 表 | 第三阶段 Synthesis schema 必须使用 FK/证据关联，不接受模糊来源。 |
| 22 | Synthesis 不自动成长期知识 | 本阶段无直接写 Fact FK | promotion 创建 document，再走派生/审核。 |
| 23 | 所有变化可审计 | append-only `knowledge_events` triggers、derivation/job FK、merge events | 领域状态变化与事件必须在同一事务；多态对象存在性由服务验证。 |
| 24 | 普通运行有界 | schema 不可表达 | CLI/JobQueue 保留 limit、dry-run、显式 unlimited。 |

注：第 11 行“检测”若实现时拼写为代码标识，统一使用 `detect`；表格中的中文描述不构成 API 命名。

## 14. 失效事务

document 内容或 normalization version 变化时，服务以一个有界事务批次：

1. 插入新的 `document_text_versions`；
2. 将旧 version 标记 invalidated；
3. 将指向旧 version 的 Mention 标记 invalidated，将 Evidence 置 stale；
4. 找出失去最后一条 valid Evidence 的 Fact，原子更新为 `pending + NULL`；
5. 将相关 FactRelation 转 pending + NULL 或标记待重评；
6. 写入审计并提交；
7. 提交后才创建新的 bounded extraction job。

若仍有其他来源的 valid Evidence，Fact 保持原状态。Synthesis stale 传播需要未来 synthesis dependency 表，本设计只要求在事务输出中产生受影响 fact IDs，不能假装六张表已经解决该依赖。

## 15. 七个验收场景交叉验证

### 15.1 正常抽取与发布

新标准化正文先写 `document_text_versions`。Mention、Entity、Alias、Fact 和 Evidence 以 pending 写入，Evidence offset 通过精确切片验证。Entity 被接受后，`facts_before_publish` 确认 subject/object Entity accepted 且 Evidence valid；单 UPDATE 写 `accepted + active`。若顺序错误，trigger 中止事务。该场景可自然表达。

### 15.2 冲突检测

两个 accepted Fact 已存在。服务写规范排序的 pending `conflicts_with` 和 relation Evidence；deterministic validation passed 后在同一事务把两个 active Fact 更新为 disputed。partial UNIQUE 阻止 A-B/B-A 重复。关系仍 pending，人工裁决后再接受/拒绝并处置 Fact。该场景可自然表达。

### 15.3 Supersession

服务以有向 `left=new, right=old` 建 pending supersedes；recursive CTE 检测无环。人工接受关系并把旧 Fact 更新 superseded，新 Fact active。旧 Fact、Evidence 和关系均保留。该场景可自然表达。

### 15.4 Entity 合并与撤销

人工 merge 事务写 `entity_merge_events(action='merge')`、对应 `knowledge_events`，并只设置 loser pointer。Fact 外键不更新，读取经 `canonical_entities` 解析。undo 写新事件并清空 pointer；FK 和 no-delete 策略保留历史。该场景可自然表达。

### 15.5 Evidence 失效重建

旧 text version、Mention 和 Evidence 保留并 stale；新 version 建新行。若 Fact 失去最后一条 valid Evidence，trigger/服务事务将其 pending + NULL，相关综合依赖由受影响 ID 列表标 stale。新 Evidence 通过后重新审核发布。Schema 能表达知识侧流程；Synthesis dependency 表属于后续设计，当前只能明确接口契约。

### 15.6 多来源佐证

`uq_fact_live_key` 把同一工作身份归到一个 pending/accepted Fact；第二来源增加新的 `fact_evidence`。唯一 Evidence span 只防同一文本重复，不阻止跨 document 佐证。一个来源 stale 后，另一个 valid Evidence 保持 Fact active。该场景可自然表达。

### 15.7 Job lease 丢失

领域表的发布发生在 worker 重新 heartbeat/确认 lease 后。现有 JobQueue 所有权检查仍是权威；目标 `job_scopes` 不改变 lease 行。失去 lease 的 worker 不进入领域写事务。Schema 本身不能识别 worker ownership，因此该场景依赖现有 JobQueue + 服务调用顺序，设计没有制造第二套 lease。

## 16. 查询视图建议

正式实现可增加但本阶段不落地：

```sql
CREATE VIEW current_facts AS
SELECT fact.*
FROM facts AS fact
WHERE fact.review_status = 'accepted'
  AND fact.knowledge_status IN ('active','disputed')
  AND EXISTS (
      SELECT 1 FROM fact_evidence AS evidence
      WHERE evidence.fact_id = fact.id
        AND evidence.validation_status = 'valid'
  );
```

Repository 默认查询只读 `current_facts` 并联结 canonical entity view。历史/轨迹 API 直接读 `facts`，但必须显式请求 superseded/retracted。Candidate review API 显式读 pending/rejected，不得复用默认查询。

## 17. 迁移边界与实施顺序建议

本文件不创建迁移。未来 migration 计划至少要拆分为：

1. 建 `document_text_versions` 并从当前 documents 回填一版快照；
2. 建六张核心领域表及辅助表/索引；
3. 建 triggers/views 并跑非法状态测试；
4. 重建 jobs 为 nullable document anchor + scope hash，再适配 JobQueue；
5. 增加 repository/service，不让 CLI 直接写 SQL；
6. 最后接 extraction pipeline。

在第 4 步 JobQueue 适配完成前，不能入队跨域 job；在 text version 回填和 validator 完成前，不能推广任何 Fact。

## 18. 已解决的生命周期未决问题

- **原 #2 jobs scope：** nullable document anchor + `job_scopes` + `scope_hash` 幂等键；需重建 jobs，留到路线图第 4 步实现。
- **原 #4 accepted + NULL：** 行内 CHECK + 单 UPDATE + BEGIN IMMEDIATE；跨表前置条件用 trigger 和服务事务。
- **原 #6 excerpt/offset：** 不可变 text version；第一版 NFKC 并由 normalization version 标识；Unicode code point、零基半开 offset；精确 slice 与 whitespace grounding 双验证。
- **原 #8 Entity 状态：** 不增加 active/merged 状态列，由 review status + merge pointer + canonical view 派生。
- **原 #5 Fact 身份：** 给出 canonical tuple SHA-256 工作定义，明确待真实数据验证、算法必须版本化。

## 19. 与生命周期规范的冲突或待澄清点

1. **Synthesis 依赖尚无表。** 生命周期要求 Fact 变化标记依赖 Synthesis stale；本阶段范围没有 synthesis schema。后续必须设计 `synthesis_runs` 与 `synthesis_dependencies`，当前只输出受影响 Fact ID，不能宣称已实现传播。
2. **normalization-only 何时入队仍按生命周期原 #1 保留。** 本 schema 能表达版本与 stale，但不决定自动 deterministic rebuild 还是显式 bounded migration；无论哪种都禁止隐式全库模型调用。
3. **现有 RelationPipeline 未使用 JobQueue。** FactRelation schema 不改变这个事实；接入方式仍需在路线图第 4 步前决定。
4. **Fact key 与 Entity 合并存在张力。** 历史 key 不改写符合历史保留原则，但合并后可能出现多个 live key 语义相同。第一版以有界重复报告和人工 reconcile 处理，需真实数据验证后决定是否引入 identity alias 表。

## 20. Schema 评审门槛

进入 migration/JobQueue 计划前必须确认：

1. 六张核心表、六张辅助表与一个查询视图的职责边界可接受；
2. `document_text_versions` 的存储成本换取历史 offset 可验证性是有意选择；
3. Fact key 第一版足以开始真实数据评估；
4. jobs 重建方案不会破坏现有 article job 的幂等与恢复；
5. 所有 24 条不变量均有数据库或应用层责任人；
6. 七个验收场景不存在被 schema 隐式阻断的路径；
7. 本文列出的冲突/待澄清点有明确后续设计归属。
