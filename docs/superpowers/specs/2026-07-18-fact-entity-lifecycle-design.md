# Fact / Entity 知识层术语与生命周期设计

**状态：** 待交叉评审；本文件冻结术语、状态机、权限边界与不变量，不授权数据库迁移或实现。

**修订 2026-07-19：** 明确 Fact 发布前 Entity 必须 accepted；确认 `refines` 为有向冻结关系；确认已验证的 pending `conflicts_with` Candidate 可直接触发相关 Fact disputed。

**适用范围：** Second-Brain 的 Entity、Alias、Mention、Fact、Evidence、FactRelation 与 Synthesis。现有 Raw / Index / Derived / Interface 四层架构保持不变；本设计把结构化知识作为 Derived 层中建立在文档派生之上的新派生类型，并在 Interface 层为未来的 `pkb think` 提供约束。

## 1. 目标与非目标

本设计的目标是在 schema 落地前，明确知识对象如何产生、审核、发布、失效、合并、撤回和保留历史。知识层必须复用现有的增量索引、版本化派生、证据校验、`job_type` 路由、`input_hash`、`pipeline_version`、lease、heartbeat、retry 与 dead-letter 机制，不建立平行的任务基础设施。

本设计不定义具体 SQL、迁移顺序、ORM、提示词正文、实体链接评分公式、自动接受阈值或 `pkb think` 的最终 CLI 参数。它也不改变现有 Raw 证据不可变、SQLite 为结构化真相、派生产物可重建、Obsidian 为确定性投影的边界。

## 2. 与现有四层架构的关系

- **Raw：** 保存不可变的来源记录和原始定位信息，是最终证据来源。
- **Index：** 保存标准化 document、来源成员关系、稳定身份、哈希和检索投影。Mention 与 Evidence 必须绑定这里的具体标准化版本。
- **Derived：** 现有 article derivation 和 document relation 之外，新增实体候选、mention、事实候选、事实证据、事实关系候选及其审计运行。它们复用现有版本化派生和 job 恢复约定，但使用各自的领域投影。
- **Interface：** 搜索、MCP、Obsidian 和未来 `pkb think` 只能读取已发布知识；候选、争议、过期和撤回状态必须按本设计过滤或显式透出。

## 3. 核心术语

### 3.1 Entity

Entity 是跨 document 持续存在、可被多个来源共同指向的规范化对象，例如 Person、Organization、Project、Concept、Decision、Event 或 Claim。Entity 不替代现有 document；document 是证据容器，Entity 是从一个或多个 document 中识别并链接出的知识对象。

### 3.2 Alias

Alias 是 Entity 的一个可观察名称或稳定标识，例如姓名变体、组织简称、邮箱、平台对象 ID、规范 URL。Alias 必须保留来源和审核信息，不以数组形式隐藏在 Entity 行中；强标识可用于确定性链接，弱名称只能生成链接或合并候选。

### 3.3 Mention

Mention 是某个 document 的特定标准化正文版本中，指向或可能指向 Entity 的文本片段。Mention 必须绑定 `document_id`、`normalized_content_hash`、`normalization_version` 和可验证的文本位置或片段；无法可靠链接时允许保留为 entity 未解析的候选。

### 3.4 Fact

Fact 是以 subject–predicate–object 表达、具有时间和知识状态的结构化陈述。subject 必须是 Entity；object 必须恰好是另一个 Entity 或一个带类型的 JSON 值。Fact 是 Derived 层的可重建知识投影，不覆盖 Raw document，也不等同于模型生成的摘要。

### 3.5 Evidence

Evidence 是 Fact 与一个具体 document 标准化版本之间的溯源链接，包含能在该版本正文中验证的 excerpt。一个 Fact 可以有多条支持、反驳或上下文证据；没有至少一条当前有效证据的 Fact 不得发布为 active knowledge。

### 3.6 FactRelation

FactRelation 是两个 Fact 之间的显式语义关系，至少包括 `conflicts_with`、`corroborates`、`supersedes` 和 `refines`。关系自身具有候选、审核、知识状态、置信度、解释、证据与派生版本，不能用 `facts` 表上的单个外键替代。

### 3.7 Candidate

Candidate 是尚未被正式发布为知识的 Entity、链接、Fact、FactRelation、合并或处置建议。LLM 与后台维护任务只能产生 Candidate；`review_status` 为 `pending` 或 `rejected` 的对象没有 `knowledge_status`，也不得进入默认知识查询。

### 3.8 Synthesis

Synthesis 是未来 `pkb think` 在检索 document、Fact 和关系后生成的结构化综合结果，至少包含 answer、逐项 evidence、conflicts、staleness flags 与 knowledge gaps。普通 Synthesis 是可失效的运行审计，不自动成为长期知识；只有经显式提升的结果才转为 analysis 或 claim document 并重新进入现有派生管线。

## 4. 通用状态模型

### 4.1 `review_status`

`review_status` 表示候选是否经过发布判断，只允许以下状态：

- `pending`：候选尚未被接受或拒绝。
- `accepted`：候选已通过规则允许的自动接受或人工审核，可以进入知识生命周期。
- `rejected`：候选被拒绝，仅作为审计历史保留。

冻结的状态转移为：

```text
pending -> accepted
pending -> rejected
accepted -> pending  # 仅限重新审核或证据失效触发
```

`rejected` 没有直接恢复路径。若后续证据支持相同主张，应创建可追溯的新候选，或由未来明确设计的重新审核操作产生新版本；不得静默把旧 rejected 行改成 accepted。

### 4.2 `knowledge_status`

`knowledge_status` 表示已接受对象在当前知识视图中的有效性，只允许以下状态：

- `active`：当前可作为默认知识使用。
- `disputed`：抽取和证据可接受，但存在未裁决冲突。
- `superseded`：曾经有效，现已被时间上更新或更精确的事实取代。
- `retracted`：事实或关系本身被撤回、判错或不再被系统认可为成立。

冻结的状态转移为：

```text
active -> disputed
active -> superseded
active -> retracted
disputed -> active
disputed -> superseded
disputed -> retracted
```

`superseded` 和 `retracted` 是终态；如需恢复，应通过新的候选和审核记录表达，而不是回写历史终态。候选阶段没有 `knowledge_status`。

### 4.3 合法组合矩阵

下表完整覆盖三个 `review_status` 与空值及四个 `knowledge_status` 的笛卡尔积。

| review_status | knowledge_status | 合法性 | 原因 |
|---|---|---|---|
| pending | （空） | 合法 | 尚未进入知识层。 |
| pending | active | 非法 | 未审核候选不能成为默认知识。 |
| pending | disputed | 非法 | 未发布候选不存在知识层争议状态。 |
| pending | superseded | 非法 | 未曾发布的候选不可能被取代。 |
| pending | retracted | 非法 | 未曾发布的候选应拒绝，而不是撤回。 |
| rejected | （空） | 合法 | 仅保留审计。 |
| rejected | active | 非法 | rejected 候选永远不能成为 active knowledge。 |
| rejected | disputed | 非法 | 被拒绝对象不参与知识层冲突。 |
| rejected | superseded | 非法 | 被拒绝对象从未成为有效知识。 |
| rejected | retracted | 非法 | 拒绝发生在发布前，撤回发生在发布后。 |
| accepted | （空） | 非法 | accepted 必须在同一事务中正式发布并获得知识状态；重新审核期间应转换为 pending。 |
| accepted | active | 合法 | 已接受且当前有效。 |
| accepted | disputed | 合法 | 抽取已接受，但知识主张存在未裁决冲突。 |
| accepted | superseded | 合法 | 曾经有效，现被更新事实取代。 |
| accepted | retracted | 合法 | 曾经发布，后被撤回或判错。 |

## 5. Entity 生命周期

Entity 的审核状态遵循通用 `review_status`。Entity 本身不使用 Fact 的 `knowledge_status`；其持续性由 active canonical、merged alias 与审计保留语义表达，具体字段名留待 schema 评审。

### 5.1 状态转移

1. 未解析 Mention -> Entity Candidate：由确定性规则、LLM 候选或后台维护任务创建，状态为 `pending`。
2. `pending -> accepted`：强标识唯一且无冲突时可由确定性规则自动接受；其他情况由人工审核。
3. `pending -> rejected`：由人工审核拒绝；确定性验证失败可拒绝纯机械候选，但必须保留原因。
4. accepted Entity A + accepted Entity B -> merge Candidate：只能由 LLM 或后台维护任务提出，不能直接合并。
5. merge Candidate `pending -> accepted`：仅人工审核；接受后 loser 的 `merged_into_entity_id` 指向 winner。
6. 已接受合并 -> 撤销合并：仅人工审核；清除该次合并建立的 `merged_into_entity_id`，保留合并与撤销审计事件。
7. accepted -> pending：仅当支撑 Entity 身份的证据失效或显式重新审核时触发；不得由普通名称相似度波动触发。

### 5.2 合并解析

合并不改写历史 Fact、Mention 或 Evidence 中保存的 Entity ID。所有读路径在比较、聚合或返回 Entity 前，必须解析 `merged_into_entity_id` 链得到 canonical Entity。这样撤销合并只需撤销合并指针，无需逆向修复历史行，也避免把错误合并传播为不可恢复的数据重写。

## 6. Fact 生命周期

### 6.1 状态转移与触发者

1. document 变化 -> Fact Candidate：由 fact-extraction job 调用 LLM 产生，`review_status=pending`、`knowledge_status=NULL`。
2. Candidate 结构与 excerpt 验证：由确定性规则执行。失败候选不得发布，并记录为 rejected 或无效派生运行。
3. `pending -> accepted + active`：仅在未来冻结的低风险自动接受策略满足时由确定性规则执行，否则由人工审核；转换必须原子完成，且 subject 与 Entity 类型 object 必须已经 accepted。
4. `active -> disputed`：`conflicts_with` Candidate 一旦通过结构、端点与 Evidence 校验，即可由确定性冲突检测自动标记相关 Fact disputed；不要求该 FactRelation 先经人工 accepted，但检测不得决定哪一条 Fact 为真。
5. `disputed -> active`：仅人工完成冲突裁决，且必须记录裁决依据与关系处置。
6. `active|disputed -> superseded`：有明确时间顺序和充分证据时，LLM 或后台维护任务只能提出 supersedes Candidate；默认由人工审核后生效。
7. `active|disputed -> retracted`：仅人工确认事实被撤回或判错后执行。
8. `accepted -> pending`：来源证据失效或人工发起重新审核时执行，同时清空 `knowledge_status`，阻止该事实继续进入默认查询。

### 6.2 时间语义

`valid_from` / `valid_to` 表达事实对现实世界有效的区间；`observed_at` 表达来源何时观察或记录该事实。新的 observed time 不自动证明 supersession。只有 predicate 语义、有效时间与证据共同支持“旧事实曾成立、新事实后来成立”时，才能提出有向 `supersedes` 候选。

## 7. FactRelation 生命周期

### 7.1 通用流程

1. 两个 accepted Fact -> FactRelation Candidate：由确定性规则、LLM 候选或后台维护任务产生，初始为 `pending` 且无 `knowledge_status`。
2. 结构、端点、证据和规范方向校验：由确定性规则执行。
3. `pending -> accepted + active`：确定性规则只可自动接受低风险的机械关系；冲突与 supersession 的最终语义默认需要人工审核。通过结构、端点与 Evidence 校验的 pending `conflicts_with` Candidate 已足以触发相关 Fact disputed，但该状态变化不等于接受关系或裁决冲突。
4. `active -> disputed`：关系本身受到相反证据挑战时，由确定性检测或后台维护任务标记。
5. `disputed -> active`、`active|disputed -> superseded|retracted`：遵循通用知识状态机；冲突裁决、撤回和 supersession 生效均需人工审核。
6. `accepted -> pending`：任一端点 Fact 或支撑 Evidence 失效时触发重新审核。

### 7.2 对称与有向关系

- `conflicts_with` 与 `corroborates` 是对称关系，写入前由应用层排序端点，并由数据库 `CHECK (left_fact_id < right_fact_id)` 保证规范方向；唯一约束防止重复。
- `supersedes` 与 `refines` 冻结为有向关系，不排序端点。`A refines B` 表示 A 对 B 作了更精确或更细粒度的表达，反向语义不等价；`supersedes` 图必须无环。
- 关系不得连接 Fact 自身。
- 一个 Fact 可与多个 Fact 冲突、互相佐证或形成时间序列，因此关系必须独立成表。

`refines` 的方向性是已确认的冻结决定，不是未决问题。

## 8. 触发者与权限表

| 操作 | 确定性规则 | LLM 候选 | 人工审核 | 后台维护任务 |
|---|---|---|---|---|
| 从标准化正文识别 Mention | 可执行 | 可提出 | 可修正 | 可重新扫描 |
| 用唯一强标识链接 Mention | 可自动执行 | 不适用 | 可修正/撤销 | 可发现失效 |
| 用名称或上下文链接 Entity | 只生成候选 | 只生成候选 | 决定 | 只生成候选 |
| 创建 Fact Candidate | 校验与去重 | 可创建 | 可手工补充 | 可创建重评候选 |
| 发布低风险 Fact | 仅按未来批准策略 | 禁止 | 可执行 | 禁止 |
| 创建 conflicts_with Candidate | 可检测 | 可提出 | 可提出 | 可提出 |
| 将相关 Fact 标为 disputed | 可执行 | 禁止 | 可执行 | 可发出失效/争议标记 |
| 裁决冲突 | 禁止 | 禁止 | 必须人工 | 禁止 |
| 提出 supersedes | 可检测候选 | 可提出 | 可提出 | 可提出 |
| 使 supersedes 生效 | 禁止默认自动执行 | 禁止 | 必须人工 | 禁止 |
| 合并 Entity | 禁止 | 禁止 | 必须人工 | 禁止 |
| 撤销 Entity 合并 | 禁止 | 禁止 | 必须人工 | 禁止 |
| 撤回 Fact | 禁止 | 禁止 | 必须人工 | 禁止 |
| 标记版本失效 | 可执行 | 禁止 | 可执行 | 可执行 |
| 改写或删除历史记录 | 禁止 | 禁止 | 禁止 | 禁止 |

已冻结：`conflicts_with` Candidate 通过确定性的结构、端点与 Evidence 校验后即可触发相关 Fact 转为 disputed，不要求先人工接受该 FactRelation；这只表示存在已验证冲突，不构成冲突裁决。

## 9. 自动化权限边界

1. 平台稳定身份、规范 URL、经规范化且唯一的邮箱等确定性强标识可以自动链接 Mention。
2. LLM 只能生成候选，不能直接合并 Entity、裁决冲突、撤回 Fact 或执行 supersession。
3. 冲突检测可以自动把已接受 Fact 标记为 `disputed`，但不得自动选择胜者。
4. 明确时间顺序且证据充分时，只能提出 `supersedes` 候选；默认需人工审核后生效。
5. Entity 合并、Fact 撤回、冲突裁决与合并撤销必须人工确认。
6. 后台维护任务只能生成候选或失效标记，不能改写历史记录。
7. 实体链接首版不得依赖 embedding。允许的确定性或可解释信号包括规范化 alias、平台身份、URL、邮箱、Entity 类型、共同来源和字符串相似度；弱信号只能形成候选。
8. 任何自动接受策略都必须在实现前另行冻结风险分级、阈值、回滚和验收数据。本文件不授权以模型 confidence 直接自动发布。

## 10. Job 与版本化派生约定

知识层复用现有 `jobs` 状态机：`pending`、`running`、`succeeded`、`failed`、`dead-letter`；claim 使用原子事务和有时限 lease，worker 通过 heartbeat 延长 lease，失效 lease 可被重新 claim，超过尝试上限进入 dead-letter。

- `jobs.job_type` 区分 article、fact extraction、entity linking、fact relation 与 synthesis 等工作类型。
- `jobs.input_hash` 标识本次工作的稳定输入；`jobs.pipeline_version` 保持现有调度命名，不在 job 表中另造 `prompt_version` 同义字段。
- 每类已接受派生仍记录现有风格的 `kind`、`schema_version`、`prompt_version`、`provider`、`model`、`source_content_hash`、`normalized_content_hash`、`normalization_version` 与 `input_hash`。
- 共用的是调度、恢复、版本和审计基础设施；不同任务写入各自领域表，不把所有异构结果塞进一个无约束 JSON 黑箱。
- worker 在 provider 返回后、推广任何不可信结果前，必须重新确认 lease 所有权；丢失 lease 的结果不得发布。
- 相同 `job_type + document/scope + input_hash + pipeline_version` 必须幂等；prompt 或 schema 版本变化创建新派生，不删除旧派生。

## 11. 哈希与失效传播

### 11.1 内容变化

`source_content_hash` 变化表示来源知识字段改变。标准流程冻结为：

```text
source content 改变
  -> 旧 Mention / Evidence 标记 stale
  -> 创建新的 fact-extraction job
  -> 重新抽取 Candidate
  -> 重新评估相关 Fact 与 FactRelation
  -> 标记依赖的 Synthesis run stale
```

旧 Mention、Evidence、Fact、Relation 和 Synthesis 审计记录必须保留。失效只影响它们能否参与当前知识视图，不覆盖旧版本。

### 11.2 标准化变化

`normalization_version` 变化时，即使 `source_content_hash` 不变，也必须重建 Mention 与 Evidence，因为 excerpt 的标准化正文绑定和 offset 已不再可信。旧记录保留其原有 `normalized_content_hash` 与 `normalization_version` 并标记 stale；新记录绑定新版本。

若标准化变化导致 `normalized_content_hash` 改变，Fact 内容未必改变，但所有依赖旧 excerpt 或 offset 的证据必须重新验证。在新 Evidence 发布前，依赖证据不足的 accepted Fact 转回 `review_status=pending`、`knowledge_status=NULL`，其相关 Synthesis 标记 stale。

### 11.3 失效粒度

- Mention 失效不自动删除 Entity。
- 一条 Evidence 失效不必然使 Fact 失效；只要仍有至少一条当前有效证据，Fact 可继续保持原知识状态并记录证据减少。
- Fact 失效或知识状态变化使直接依赖它的 FactRelation 与 Synthesis stale；传播必须可追踪且有界，不能用一次普通 index build 启动无界模型调用。
- Entity 合并或撤销不改写 Fact，因此不使原始 Evidence 失效；它只使 canonical Entity 解析结果和依赖该解析快照的 Synthesis 失效。

## 12. 数据库与应用层不变量

以下约束是 schema 与服务实现必须共同保证的硬性规则：

1. 历史 Fact 不得物理删除。
2. 发布的 Fact 必须至少有一条当前有效的 `fact_evidence`。
3. Evidence excerpt 必须存在于其绑定的 `normalized_content_hash + normalization_version` 标准化正文中；保持现有去空白后包含校验的最低语义，是否增加精确 offset 校验留待实现设计。
4. Mention 必须绑定 `normalized_content_hash` 与 `normalization_version`。
5. Fact object 必须恰好使用 `object_entity_id` 或 `object_value_json` 之一，不可都空或都非空。
6. Fact subject 必须使用 Entity；Fact 不得以裸字符串代替已解析 subject。
7. Fact 发布为 accepted 前，其 `subject_entity_id` 与非空 `object_entity_id` 指向的 Entity 必须已经 accepted；Fact 不得引用 pending 或 rejected Entity 进入 active knowledge。
8. `object_value_json` 必须携带受控类型，不能成为任意无 schema JSON。
9. `conflicts_with` 必须满足 `left_fact_id < right_fact_id`；应用层写入前排序，数据库使用 CHECK 和唯一约束双重保证。
10. 对称关系 `conflicts_with`、`corroborates` 不得重复插入，也不得连接自身。
11. `supersedes` 保持有向且不得形成环；环检测是写入事务的前置条件。
12. `refines` 保持有向；`A refines B` 与 `B refines A` 是不同主张，不得按对称关系排序或去重。
13. Entity 的 `merged_into_entity_id` 链不得形成环，并必须能解析到唯一 canonical Entity。
14. Entity 合并不得物理改写历史 Fact 的 `subject_entity_id` 或 `object_entity_id`，也不得改写历史 Mention/Evidence 的 Entity 引用。所有读路径必须先解析 canonical Entity。
15. `review_status` 与 `knowledge_status` 必须满足合法组合矩阵；`pending` 和 `rejected` 的 `knowledge_status` 必须为空。
16. `pending -> accepted` 必须与初始 `knowledge_status=active` 在同一事务提交，不能暴露 accepted + NULL 中间态。
17. `accepted -> pending` 必须在同一事务清空 `knowledge_status` 并失效默认查询投影。
18. rejected Candidate 不得进入默认查询、关系生成或 Synthesis 上下文。
19. disputed Fact 不得被 `pkb think` 表述为无保留的确定结论，必须携带 disputed 标记和冲突证据。
20. retracted Fact 不进入默认当前知识查询；superseded Fact 仅在历史或轨迹查询中返回，除非调用者显式请求。
21. Synthesis 的每个重要 claim 必须关联具体 `fact_id` 或 `document_id + excerpt`；模糊来源名称不构成证据。
22. 普通 Synthesis run 不自动成为长期知识；提升必须显式发生，并生成新的 analysis 或 claim document。
23. 所有 Candidate、审核、状态变化、合并、撤销和失效操作必须记录操作者/触发者、时间、原因和关联派生版本。
24. 任何普通增量运行都必须有界；无界模型调用需要沿用现有显式 unlimited 授权模式。

## 13. 删除、合并、撤销与历史保留

### 13.1 删除

知识对象没有普通物理删除操作。错误候选使用 rejected，已发布错误使用 retracted，过时事实使用 superseded，版本失效使用 stale 标记。仅允许按未来单独制定的隐私擦除或损坏修复流程做物理删除；该流程不属于本设计授权范围。

### 13.2 Entity 合并与撤销

合并只建立 loser -> winner 的 `merged_into_entity_id` 指针和审计事件。Fact、Mention、Evidence 与历史 Synthesis 中的原始 Entity ID 保持不变；读取时解析 canonical 链。撤销合并由人工确认，移除对应指针并记录撤销事件，不需要批量逆向修改历史事实。

如果 loser 已继续被其他 Entity 合并，撤销必须先验证不会产生分叉、环或不可解释的 canonical 结果；复杂链调整必须作为新的人工审核操作，不得静默重排。

### 13.3 Fact 撤回与取代

- retracted 表示主张被撤回、证据被认定不可靠或事实本身判错。它不表示“后来发生了变化”。
- superseded 表示旧主张曾经有效，但在时间或精度上被新 Fact 取代。旧 Fact 仍用于历史轨迹。
- 两种操作都保留原 Fact、Evidence、关系和审计信息；不得用新 Fact 覆盖旧行。

### 13.4 Synthesis 历史

Synthesis run 保存其检索快照、输入哈希、模型与 prompt 版本以及证据引用。依赖对象变化时标记 stale，不批量重写旧答案。用户显式提升的 Synthesis 成为新 document 后，按普通 document 的版本化派生和失效规则管理。

## 14. Synthesis 与 `pkb think` 约束

未来的结构化输出至少包含：

```json
{
  "answer": "...",
  "claims": [
    {
      "text": "...",
      "confidence": 0.0,
      "evidence_ids": ["fact-or-document-evidence-id"],
      "status": "supported|disputed|insufficient"
    }
  ],
  "conflicts": [],
  "staleness_flags": [],
  "knowledge_gaps": []
}
```

默认 Think 结果是临时答案加轻量审计，不自动进入长期知识。完整结果仅在用户显式保存、经用户确认的高价值建议或正式周期综合任务中持久化。任何被提升的结果必须成为 analysis/claim document，再经过 Source/Index/Derived 的正常闭环；不得从 Synthesis 直接写成 active Fact。

## 15. 最小验收场景

### 15.1 正常抽取与发布

- **输入状态：** document D 的标准化正文包含“张三于 2026 年加入 Acme”，不存在对应 Entity、Mention 或 Fact。
- **触发动作：** fact-extraction job 被 claim；LLM 产生 Mention、Entity 和 Fact Candidate；确定性验证确认 excerpt 存在且 object 使用 Entity。
- **期望结果：** Candidate 初始为 `pending + NULL`；subject 与 object Entity 先成为 accepted，Fact 才能经人工接受并原子转换为 `accepted + active`；Fact 至少关联一条有效 Evidence；job 成功并保留 `input_hash`、`pipeline_version`、prompt 与三类哈希审计。

### 15.2 冲突检测

- **输入状态：** accepted + active Fact A 表示“项目预算为 100 万”，新 Fact B 表示同一有效期预算为 120 万，两者均有有效来源证据。
- **触发动作：** 确定性候选选择与 LLM 解释产生 `conflicts_with` Candidate，应用层排序端点，数据库验证 `left_fact_id < right_fact_id`。
- **期望结果：** 关系不重复；A 与 B 可被自动标记为 `accepted + disputed`，但系统不自动选择胜者；`pkb think` 必须呈现两种说法及证据。人工裁决后胜出 Fact 可回到 active，另一 Fact 根据裁决转为 retracted 或保持有明确解释的 disputed。

### 15.3 Supersession

- **输入状态：** Fact A 为“张三在甲公司任职”，有效期截至 2026-05；Fact B 为“张三自 2026-06 起在乙公司任职”，两者均 accepted 且证据充分。
- **触发动作：** 规则检测明确时间顺序，生成有向 `B supersedes A` Candidate；人工审核确认。
- **期望结果：** 关系 accepted + active，A 转为 superseded，B 保持 active；A 不被删除且在时间轨迹查询中可见；系统拒绝任何导致 supersedes 环的写入。

### 15.4 Entity 合并与撤销

- **输入状态：** Entity E1“OpenAI”与 E2“Open AI Inc.”各自被历史 Fact 引用；弱名称和上下文信号产生 merge Candidate。
- **触发动作：** 人工确认 E2 合并到 E1，随后发现误合并并人工撤销。
- **期望结果：** 合并只设置 E2 的 `merged_into_entity_id=E1`；历史 Fact 的 subject/object ID 均不改变；读路径在合并期间解析到 E1。撤销后指针恢复为空，历史 Fact 无需修复，合并与撤销事件均可审计。

### 15.5 证据失效与重建

- **输入状态：** active Fact F 只有一条绑定 D 的旧 `normalized_content_hash=N1`、`normalization_version=1` Evidence；Synthesis S 引用了 F。
- **触发动作：** D 内容改变，或仅 normalization version 升至 2；旧 Mention/Evidence 标记 stale，并通过受控 fact-extraction 工作重建。
- **期望结果：** 旧记录保留；F 因暂时无有效证据转为 `pending + NULL`，S 标记 stale；新 Evidence 验证通过并经审核后，F 可重新发布为 accepted + active。普通 index build 不得因此产生无界模型调用。

### 15.6 多来源佐证不重复建 Fact

- **输入状态：** active Fact F 已由 document D1 支持，D2 提供同一 subject、predicate、object 与兼容有效期的独立证据。
- **触发动作：** 新抽取运行完成事实身份归一化。
- **期望结果：** 系统为 F 增加第二条 Evidence 或产生待审核的归并候选，不创建不可解释的重复 active Fact；D1 失效后，只要 D2 Evidence 仍有效，F 不转 pending。

### 15.7 Job lease 丢失时禁止发布

- **输入状态：** fact-extraction job 为 running，worker A 的 provider 调用超过 lease；worker B 已重新 claim。
- **触发动作：** worker A 收到模型结果并尝试验证和发布。
- **期望结果：** worker A 在推广结果前 heartbeat/所有权检查失败，不能写入 accepted Fact 或当前投影；worker B 可继续处理；lease_expired 与后续事件可审计。

## 16. 规范验收条件

进入 schema 设计前，交叉评审必须确认：

1. 所有领域对象和现有 document / derivation / relation 的边界无歧义。
2. 合法组合矩阵可由数据库约束和服务事务共同实现。
3. 状态转移均有唯一允许的触发者类别，且没有 LLM 越权路径。
4. Entity 合并与撤销无需改写历史 Fact。
5. 内容和 normalization-only 变化均不会留下可被默认查询使用的失效 Evidence。
6. 任何 Synthesis claim 均可回溯到具体 Fact 或 document excerpt。
7. 普通增量运行保持有界、可恢复、可审计和幂等。

## 17. 未决问题 / 需要交叉评审确认的点

1. **normalization-only 重建的触发方式存在既有约定冲突。** 现有架构规定 normalization-only 变化只把旧 derivation 标记为 `normalization_stale`，普通 index build 不自动入队，需显式 bounded migration；本规范冻结 Mention/Evidence 必须重建，但尚需确认是自动创建不调用模型的重建 job、显式迁移入队，还是分成确定性 offset 重建与 LLM 重新抽取两步。无论选择哪种方式，都不得静默触发全库 AI 调用。
2. **现有 `jobs.document_id` 为必填。** Entity merge、跨 Fact relation 和 Synthesis 可能不是单 document scope。需要评审是扩展现有 job scope，还是仍以锚点 document 建 job 并另存 scope；不得在本规范阶段自行改 schema。
3. **现有 relation pipeline 未完全复用 `JobQueue`。** 新 FactRelation 是否必须统一进入通用 job lease，或保留 bounded 同步 pipeline，需要在实现计划前确认。
4. **`accepted + NULL` 的数据库实现。** 本规范把它定义为非法并要求事务内发布；需评审 SQLite CHECK、延迟约束能力和服务事务如何共同避免中间态。
5. **Fact 身份键尚未冻结。** subject、predicate、typed object、有效期和来源观察之间哪些字段组成稳定身份，会影响多来源佐证、重复候选与 supersession，需用真实数据评审。
6. **Evidence excerpt 与 offset 的精确规则。** 当前实现采用去空白后的 substring 验证；Mention 要求 offset。需确认 Unicode 规范化、重复片段和 offset 单位（Unicode code point 或 UTF-8 byte），同时保持与现有验证兼容。
7. **自动接受范围尚未批准。** 除唯一强标识 Mention 链接外，本文件不批准任何 confidence 阈值。Fact 或 FactRelation 的低风险自动接受清单必须依据真实评估集另行评审。
8. **Entity 本体的状态字段。** 本规范不让 Entity 复用 Fact 的 knowledge status，但 schema 仍需决定 active/merged 等是派生字段、查询视图还是独立 lifecycle 字段。
9. **Synthesis 审计保留策略。** 需确认默认是否保存完整 answer、仅保存哈希与证据清单，及私人临时查询的保留期限和删除权；本规范只冻结其不得自动成为长期知识。
