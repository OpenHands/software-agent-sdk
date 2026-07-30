# Workflow Agent 上下文压缩方案

> 状态：设计稿，供 Demo 之后的正式实现评审使用  
> 日期：2026-07-29  
> 范围：Pyromind Agent 持续生成、修改和校验 workflow JSON/DSL 的长会话

## 1. 结论

不建议只根据用户消息数量或 Event 数量触发压缩。

Workflow 场景的上下文增长主要来自节点 JSON、工具返回、校验错误和重复读取，
同样 20 个 Event 的 Token 数量可能相差几十倍。推荐采用：

1. **Token 使用率作为主触发条件**。
2. **Event 数量作为异常兜底，不作为主要容量指标**。
3. **以用户轮次和完整工具调用为压缩边界**，不从任意 Event 中间切割。
4. **开头的系统提示词始终保留，不进入压缩范围**。
5. **当前轮和最近 3 个完整用户轮次始终保留原文**，只压缩中间旧对话。
6. **Workflow DSL 以 `workflow.py` 和已有 snapshot 为权威数据**，不交给摘要模型
   压缩或重写；需要时由 Agent 重新读取当前文件。
7. 压缩只替换发给模型的中间历史 `View`，完整 EventLog 继续持久化，支持复盘。

建议的最终模型上下文为：

```text
系统提示词与 Skill 索引
+ 历史交接摘要 ConversationSummary
+ 最近 3 个完整用户轮次
+ 当前正在执行的用户轮次
```

## 2. Codex 的做法

以下结论来自本地 `codex-main` 源码：

- `codex-rs/core/src/session/context_window.rs`
- `codex-rs/core/src/session/turn.rs`
- `codex-rs/core/src/compact.rs`
- `codex-rs/prompts/templates/compact/prompt.md`

### 2.1 触发条件

Codex 主要根据有效上下文 Token 使用量触发自动压缩：

- 在普通采样开始前检查 Token 窗口。
- Agent 在一轮中还需要继续执行时，也会检查 Token 窗口并支持 mid-turn 压缩。
- 模型切换到更小上下文窗口时可以触发压缩。
- 压缩兼容性标识变化时可以触发压缩。
- 用户可以手动请求压缩。

Codex 并不把 Event 数量作为主要触发指标。

### 2.2 压缩内容

Codex 把已有历史连同 compaction prompt 发送给模型，让模型生成面向下一模型的
handoff summary，要求摘要保留：

- 当前进度和关键决策；
- 重要上下文、限制和用户偏好；
- 尚未完成的任务及明确下一步；
- 继续工作所需的关键数据、示例和引用。

如果摘要请求本身超过上下文窗口，Codex 会从历史开头逐项移除最旧内容，优先保留
最近内容，再重新请求摘要。

### 2.3 压缩后的替换

Codex 不是简单地在完整历史末尾追加摘要，而是构造 replacement history：

```text
必要的初始上下文
+ 最近的真实用户消息
+ compaction summary
```

最近用户消息按从新到旧选择，总预算上限为约 20,000 tokens；超出预算的边界消息会
截断。旧摘要消息不会被当成真实用户消息重复保留。

因此 Codex 的两个关键设计是：

1. 使用 Token 而不是消息条数衡量容量。
2. 压缩后显式保留最近真实用户输入，摘要承担任务交接，而不是承担全部用户原文。

## 3. 当前 Demo 的做法

当前实现位于：

- `openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py`
- `openhands-sdk/openhands/sdk/context/condenser/prompts/summarizing_prompt.j2`
- `openhands-sdk/openhands/sdk/event/condenser.py`
- `openhands-sdk/openhands/sdk/context/view/view.py`
- `openhands-sdk/openhands/sdk/agent/agent.py`

### 3.1 当前触发条件

当前共有三类触发：

| 触发类型 | 当前逻辑 | 要求级别 |
|---|---|---|
| Token | 达到显式 `max_tokens`，否则达到模型输入窗口的 90% | HARD |
| Event | 当前 View 的 Event 数超过 `max_size` | SOFT |
| Request | 手动请求、上下文溢出恢复、异常历史恢复 | HARD |

Pyromind 的 Codex preset 当前配置为：

```python
max_size = 480
target_size = 120
keep_first = 4
keep_last_user_turns = 3
auto_compact_ratio = 0.9
summary_input_ratio = 0.6
```

本地 Demo 可以通过 `OH_CONDENSER_TEST_MAX_SIZE` 临时降低 Event 阈值。

### 3.2 当前选择和保留策略

Event 数触发时，目标 View 缩小到 `target_size`。以当前配置为例：

```text
压缩前：超过 480 个 View Event
压缩后：目标 120 个 View Event

组成：
- 最前 4 个 Event
- 1 个 CondensationSummaryEvent
- 按容量计算的尾部 Event
- 从倒数第 3 条用户消息开始的完整上下文；如果这部分超过目标容量，优先完整保留
```

`120` 是容量目标而不是破坏性硬上限。如果系统提示词和最近 3 个完整用户轮次本身超过
120 个 Event，保护规则优先，压缩后的 View 会大于 120。

Token 触发时，会计算需要释放的 Token，并保留满足目标 Token 数量的尾部 Event。
如果多种条件同时满足，选择压缩力度最大的范围。

切割点通过 `manipulation_indices` 调整，避免拆开模型 API 要求的原子结构，例如工具
调用和工具结果。

### 3.3 当前摘要与替换策略

中间被遗忘的 Event 会发送给摘要模型。摘要 Prompt 维护用户要求、任务状态、代码状态、
测试结果和版本控制状态等信息。

摘要输入最多占摘要模型窗口的 60%。如果仍然过大，会优先丢弃最旧的摘要输入并重试，
最多重试 5 次。

成功后产生持久化 `Condensation` Event：

```text
forgotten_event_ids
summary
summary_offset
llm_response_id
```

`View` 应用该事件时：

1. 从模型 View 删除 `forgotten_event_ids` 对应的 Event。
2. 在 `summary_offset` 插入一个合成的摘要 Event。
3. 下一次 Agent step 使用新的 View 调用模型。

完整 EventLog 不删除，因此前端和审计接口仍可读取原始历史。

## 4. 当前 Demo 的主要问题

### 4.1 Event 数不能代表上下文大小

一个只含状态文本的 Event 可能只有几十个 Token，一个 workflow JSON 或工具输出可能有
几万个 Token。当前采用 480 Event 作为较宽松的近似阈值，同时保留 Token 窗口硬保护。

### 4.2 最近用户轮次可能本身很大

当前从倒数第 3 条用户消息开始完整保留，因此不会丢失最近三轮的 Skill、工具调用和工具
返回。代价是如果最近三轮本身非常大，普通摘要不能继续缩小这部分，需要依赖大工具输出
外置或模型 Token 窗口硬保护。

### 4.3 Workflow 状态不适合只用自然语言摘要保存

节点 ID、边 ID、端口、参数、数据路径和版本 Hash 必须精确。LLM 摘要适合保存意图和
决策，不适合作为 Workflow 真值来源。

### 4.4 摘要输入超限时会优先丢掉最老内容

这能保证压缩继续进行，但如果最初用户约束仅存在于旧 Event 中，约束可能退出摘要输入。
因此关键约束需要结构化固定，而不能只依赖滚动摘要。

### 4.5 首部固定保留 Event 的语义不稳定

`keep_first=4` 只表达位置，不表达业务含义。前四个 Event 不一定就是最值得永久保留的
用户要求。

## 5. Workflow 场景推荐方案

### 5.1 把上下文拆成三类数据

#### A. 不压缩的系统上下文

由系统每轮重新注入，不放在会话摘要中：

- 系统 Prompt；
- Workflow DSL 规则；
- 当前启用的 Skill 索引；
- 工具 Schema；
- 权限和安全约束。

#### B. 上下文之外的 Workflow 权威数据

Workflow DSL 不作为会话摘要的一部分，也不需要由压缩模块管理：

- 当前完整 DSL 保存在 `public_data/workflow_canvas/workflow.py`；
- 已有 Workflow snapshot 保存输入、输出版本并支持回滚和审计；
- 每次用户带上画布状态时，服务端先同步当前画布到 `workflow.py`；
- Agent 需要理解或修改 Workflow 时，重新完整读取当前 `workflow.py`；
- 历史 `file_editor` 返回的 DSL 只是上下文副本，可以随中间对话一起压缩或丢弃；
- 摘要不得尝试重建、改写或替代完整 DSL。

这样 Workflow 的正确性由文件和 snapshot 保证，而不是由自然语言摘要保证。

#### C. 会话交接摘要

LLM 摘要只保存无法从 Workflow 真值推导的内容：

- 用户目标、偏好和明确限制；
- 为什么选择当前节点结构；
- 已确认和被否决的方案；
- 当前正在处理的问题；
- 未完成步骤和阻塞项；
- 最近失败及其根因；
- 相关 Event ID、Workflow version 和 hash。

### 5.2 以用户轮次划分压缩边界

推荐引入 `ConversationTurn` 概念：

```text
一个 User Message
+ 之后的 Assistant Message
+ 该轮产生的 Tool Call / Tool Result
+ 直到下一条 User Message 之前
```

压缩时只选择完整的旧 Turn，不能在一个 Turn 内任意切割。当前正在执行的 Turn 永远不进入
普通压缩范围。

建议默认保留：

- 开头的系统提示词：永久完整保留，并按 Event 类型识别，不能只依赖 `keep_first` 位置；
- 当前 Turn：全部原文；
- 最近 3 个已完成 Turn：全部原文；
- 更早 Turn：进入交接摘要；
- 被标记为 pinned 的用户约束：进入结构化约束表，不依赖原文位置。

“最近 3 Turn”是语义保底，不是容量上限。如果最近 Turn 本身很大，仍需要对大工具输出
单独裁剪或外置。

压缩前后边界为：

```text
压缩前：系统提示词 | 较早 Turn | 中间 Turn | 最近 3 Turn | 当前 Turn
压缩后：系统提示词 | 中间历史摘要       | 最近 3 Turn | 当前 Turn
```

摘要模型只能接收被选中的中间历史，不能接收系统提示词、最近 3 Turn 或当前 Turn，避免
摘要实现意外改写本应原样保留的内容。

### 5.3 触发策略

推荐使用两级 Token 阈值：

| 条件 | 建议值 | 行为 |
|---|---:|---|
| Soft Token | 有效输入窗口的 70% | 当前 Turn 完成后压缩 |
| Hard Token | 有效输入窗口的 85% | 下一次 LLM 请求前必须压缩 |
| Event 近似阈值 | View 超过 480 Event | 在安全边界压缩 |
| 单 Event 过大 | 超过 8k Token | 立即外置/截断该工具结果 |
| 手动请求 | 用户或服务端请求 | 在安全边界压缩 |
| Context overflow | 模型明确报错 | 紧急压缩后重试一次 |

阈值应由模型能力配置给出，而不是写死在 Agent 逻辑中。生产环境不使用
`OH_CONDENSER_TEST_MAX_SIZE`。

### 5.4 Workflow 专用的大内容处理

在会话压缩之前，先控制重复内容：

1. `workflow.py` 和 Workflow snapshot 始终保留原文，不由 condenser 读取、摘要或修改。
2. Agent 通过 `file_editor` 读取完整 DSL，通过 `apply_patch` 修改权威文件。
3. 历史工具结果中的 DSL 副本可以从中间对话移除；需要时重新读取当前文件。
4. 工具结果超过 8k Token 时保存到文件或对象存储，Event 只保留路径、hash、行数和摘要。
5. 校验错误保留错误码、节点 ID、字段路径和修复状态，去掉重复堆栈。
6. 已完成工具调用的原始大输出可以裁剪，但调用结论必须进入对话摘要。

### 5.5 压缩选择算法

建议算法如下：

```text
1. 计算当前有效 View Token 数。
2. 按 User Message 将历史聚合成 Turn。
3. 锁定当前 Turn、最近 3 个完成 Turn、pinned constraints。
4. 从最旧的可压缩 Turn 开始选择，直到预计压缩后不超过 120 个 Event；Token 触发时
   同时满足低于有效 Token 阈值 50% 的目标。
5. 将选中的完整 Turn 发送给摘要模型。
6. 从摘要输入排除系统提示词、当前 Turn、最近 3 个 Turn，以及文件和 snapshot 本身。
7. 校验摘要是否保留用户要求、关键决策和未完成事项。
8. 写入 Condensation Event。
9. 重建 View，并重新计算 Token。
10. 如果仍高于 70%，继续处理大工具结果，不重复摘要刚处理的 Turn。
```

Event 触发后的目标设为 120，而不是 `480 // 2`；Token 维度仍预留约一半窗口，避免
频繁连续压缩。系统提示词和最近 3 个完整用户轮次始终优先于容量目标。

### 5.6 推荐摘要格式

```text
USER_INTENT
- 当前目标
- 明确约束
- 用户偏好

DECISIONS
- 已确认决策及原因
- 已否决方案及原因

WORKFLOW_REFERENCE
- 权威文件：public_data/workflow_canvas/workflow.py
- 当前状态：未创建、已生成、已修改或已校验
- 继续处理前是否需要重新读取文件

COMPLETED
- 已完成事项及结果

CURRENT_STATE
- 当前正在修改的节点或阶段
- 当前校验状态
- 最近一个有效 Event ID

PENDING
- 下一步
- 等待用户确认的选择

FAILURES
- 尚未解决的错误码、字段路径和原因

VERSION_CONTROL_STATUS
- 分支、提交、未提交文件
```

摘要应限制为结构化事实，不要求保存模型思维过程，不复制、恢复或改写完整 Workflow
JSON/DSL。

### 5.7 替换后的 View

建议压缩后的模型 View 为：

```text
[System / Skills / Tool schemas]
[ConversationSummary]
[最近 3 个完整历史 Turn]
[当前 Turn]
```

持久化 EventLog 保持：

```text
[全部原始 Event]
[Condensation Event]
[后续 Event]
```

`Condensation Event` 建议增加：

```text
trigger_reason
source_event_ids / source_turn_ids
preserved_turn_ids
tokens_before
tokens_after
summary_tokens
strategy_version
```

这样前端、日志和复盘工具可以准确回答“为什么压缩、压缩了什么、保留了哪些用户轮次、
压缩后节省了多少 Token”。Workflow 版本继续由已有 snapshot 系统负责。

## 6. 是否应该根据用户消息划分

建议：**根据用户消息划分压缩单元，但不根据用户消息数量触发压缩。**

原因：

- 用户消息是稳定的业务阶段边界，适合确定哪些内容应该一起摘要。
- Token 才能反映模型容量，适合决定什么时候必须压缩。
- Tool Call/Result 必须归属到触发它的用户 Turn，避免摘要后失去因果关系。
- `workflow.py` 和已有 snapshot 提供准确 Workflow 状态，用户 Turn 摘要只提供意图和
  决策，两者职责清晰。

因此应区分：

```text
什么时候压缩：看 Token，Event 只兜底
从哪里切割：看 User Turn 和工具原子边界
永久保留什么：系统提示词
原样保留什么：当前 Turn + 最近 3 Turn + pinned constraints
压缩什么：位于系统提示词和最近 Turn 之间的旧对话与工具历史
如何保证 Workflow 正确：重新读取当前 workflow.py，不从摘要恢复 DSL
```

## 7. 失败与降级策略

1. 摘要模型超限：逐个移除最旧候选 Turn 后重试。
2. 摘要调用失败：保持原 View，不写成功 Condensation Event。
3. 已到 Hard Token 阈值且摘要失败：裁剪/外置最大工具输出，再重试摘要。
4. 当前 `workflow.py` 不存在或读取失败：停止 Workflow 修改，不尝试从摘要恢复 DSL。
5. 摘要缺少关键字段：视为失败，不替换 View。
6. 紧急恢复最多重试一次正常 Agent 调用，防止无限压缩循环。
7. 多次压缩后精度持续下降：前端提示用户开启新会话；Workflow 继续从权威文件加载。

## 8. 实施阶段

### Phase 0：固定 Demo 行为

- 保留现有 EventLog、Condensation Event 和前端展示。
- 明确生产与测试阈值，测试环境变量不得进入生产配置。
- 日志补充触发原因、压缩前后 Token 和保留 Event 数。

### Phase 1：按 Turn 压缩

- 增加 User Turn 聚合器。
- 永久排除系统提示词，不允许 condenser 将其放入遗忘集合。
- 当前 Turn 和最近 3 Turn 不参与普通压缩。
- Event 阈值降级为兜底条件。
- Soft/Hard Token 阈值调整为 70%/85%。

### Phase 2：Workflow 外部状态边界

- 明确 `workflow.py` 和已有 snapshot 是唯一权威状态。
- 摘要 Prompt 明确禁止复制、恢复或改写完整 DSL。
- 历史 `file_editor` DSL 返回允许被压缩，需要时重新读取当前文件。
- 在 Workflow 相关请求开始前确保画布已同步到 `workflow.py`。

### Phase 3：质量守卫

- 摘要结构校验。
- 关键 ID 和用户约束覆盖检查。
- 压缩前后 Token 指标。
- 多轮压缩恢复测试。

## 9. 验收标准

### 功能

- 压缩后 Agent 仍能正确说出用户当前目标和未完成事项。
- 系统提示词在任意次数压缩后仍完整存在于模型 View。
- 最近 3 个用户 Turn 的原文仍在模型 View 中。
- 当前 Turn 的 Tool Call/Result 不被拆开。
- 压缩模块不修改 `workflow.py` 或 Workflow snapshot。
- 压缩后处理 Workflow 请求时，Agent 能重新读取当前完整 DSL。
- 刷新或重启后能够从 EventLog 重建相同 View。

### 容量

- Soft 压缩后上下文低于有效窗口的 55%。
- Hard 压缩后上下文低于有效窗口的 60%。
- 同一稳定 Workflow JSON 不重复占用大段上下文。
- 单个大工具输出不会阻止摘要调用。

### 可观测性

- 前端长期展示每次压缩 Event。
- 日志包含 conversation ID、condensation event ID、触发原因和耗时。
- 可以查询压缩涉及的 source Event/Turn ID。
- 可以看到 Token before/after、保留 Turn ID 和摘要模型响应 ID。

### 回归用例

至少覆盖：

1. 多轮逐步新增节点后继续修改早期节点。
2. 用户连续推翻节点方案，摘要必须保留最新决策。
3. 大型 Workflow JSON 和长校验错误触发 Token 压缩。
4. 压缩发生在工具循环附近，但不拆开调用与结果。
5. 两次以上滚动压缩后仍可继续编辑和校验。
6. 摘要模型超限、失败和重试。
7. 服务重启后从持久化 Event 重建压缩 View。

## 10. 推荐默认配置

```yaml
context_compaction:
  strategy_version: workflow_v1
  soft_token_ratio: 0.70
  hard_token_ratio: 0.85
  target_token_ratio: 0.50
  event_count_fallback: 480
  recent_turns_to_keep: 3
  max_single_observation_tokens: 8000
  summary_input_ratio: 0.60
  max_summary_retries: 3
  preserve_system_prompt: true
  preserve_current_turn: true
  preserve_atomic_tool_groups: true
  exclude_workflow_source_of_truth: true
```

这些值应先通过真实长会话采样验证，再按模型和 Workflow 平均大小调整。Demo 的小 Event
阈值只用于触发展示，不应作为生产默认值。
