# Pyromind Agent 三项改造方案（周会汇报版）

> 日期：2026-07-27  
> 目标：提高任务执行稳定性，降低工作流上下文成本，减少前端工具日志噪音。  
> 说明：本文是改造方案，不代表以下能力已经全部上线。

## 一、先说结论

这三点应该一起做，但职责不同：

1. **Skill 路由与状态边界**解决“当前任务需要哪些 Skill、按什么顺序执行，以及当前状态允许执行什么动作”。
2. **工作流摘要**解决“当前工作流是什么，不要每一步都重新阅读完整代码”。
3. **工具调用分组**解决“底层过程很多，但用户只需要看到阶段、结果和异常”。

完整关系是：

```text
用户请求
  ↓
轻量 Skill 路由：识别任务需要的最小 Skill 集合和执行顺序
  ↓
WorkflowState + PreToolUse：根据真实状态和用户授权约束高风险动作
  ↓
工作流摘要：提供当前画布的精简上下文
  ↓
Agent 按阶段调用现有 Skill 和 Tool
  ↓
前端把同一阶段的多个工具事件合并成一张进度卡
```

这不是替换 OpenHands Harness，而是在现有 Harness 上补一层 Pyromind 业务控制和展示能力。

三个改造的优先级建议是：

| 优先级 | 改造 | 原因 |
|---|---|---|
| P0 | 工作流摘要 | 收益明确，现有 `xyflow` 和 dirty 标记可直接复用，风险最低 |
| P0 | 前端工具调用分组 | 主要是展示层改造，可以最快改善用户体验 |
| P1 | Skill 路由与状态边界 | 价值最大，但需要先明确业务状态和高风险动作，再逐步开启硬限制 |

---

## 二、改造一：用轻量路由和状态机明确 Skill、Tool 边界

### 2.1 当前问题

目前主要依靠系统 Prompt 和 `SKILL.md` 让模型自行判断：

- 用户是在问问题、生成工作流、修改工作流、校验，还是调试。
- 应该选择 `generate-workflow-dsl`、`debug-workflow` 或 `data-cleaning`。
- 哪些工具可以调用，哪些工具不能调用。
- 执行到什么条件应该停止。

这种方式属于软约束。模型理解正确时可以工作，但短指令或组合指令容易走错，例如：

- “换个模型跑一下”可能同时触发修改和调试，而当前规则要求本轮只修改并校验。
- “看看这个数据”可能被当成知识问答，也可能需要真实调用 `preview_dataset`。
- 生成工作流时不应该调用数据清洗或正式运行工具，但 Prompt 不能形成不可绕过的限制。

### 2.2 Codex 的做法带来的启发

查阅本地 `codex-main` 后，可以确认 Codex 没有在每轮开始时先生成一份完整的“任务分类 JSON + allowed_tools”。它采用的是分层设计：

1. **语义选择仍交给模型**：系统只注入 Skill 的名称和简短描述，匹配后才读取完整 `SKILL.md`，不预先加载所有正文。
2. **Skill 元数据很轻**：支持是否允许隐式调用、依赖哪些工具等字段；动态 Skill 选择器也是便宜、确定性的候选召回，而且源码明确说明目前可以先以 shadow mode 运行，不直接改变模型看到的 Skill 列表。
3. **工具按当前 Turn 构建**：Tool Router 根据模式、Feature、插件和环境决定哪些 Tool 对模型可见；还支持 deferred tool，需要时再发现，而不是把所有工具都塞进 Prompt。
4. **硬边界不依赖任务分类**：工具执行前经过 PreToolUse Hook；命令还受沙箱、审批和执行策略控制。即使模型选错工具，执行层仍可以阻止。
5. **`update_plan` 只管进度**：它不参与 Skill 选择和权限判断。

这个思路比“先分类，再生成一大份执行契约”更适合当前项目：让模型负责理解语义，让程序只约束能够确定的业务状态和高风险动作。

### 2.3 推荐方案：薄路由 + 工作流状态机 + 执行前校验

#### 第一层：Skill 路由识别能力集合，按阶段激活 Skill

一个请求可能同时需要多个 Skill。例如“清洗这份数据，生成 SFT 工作流并试跑”需要依次使用：

```text
data-cleaning → generate-workflow-dsl → debug-workflow
```

因此 Router 不能把整个任务设计成三个 Skill 只能选一个。它应该识别任务需要的最小 Skill 集合、依赖关系和执行顺序；但每个阶段只激活一个 Skill，不要一次加载三个 `SKILL.md` 并让它们同时控制本轮执行。这样既支持组合任务，也避免不同 Skill 的工具限制、停止条件和异步回调互相冲突。

| 用户需求 | 所需阶段 |
|---|---|
| 修改模型、节点、参数、数据或拓扑 | 生成或修改工作流 |
| 只要求测试、调试或试跑，且当前工作流无需修改 | 调试工作流 |
| 清洗、转换或修复数据 | 数据清洗 |
| 清洗后生成并测试 | 数据清洗 → 生成工作流 → 调试工作流 |
| 只是解释概念或查询知识 | 不进入执行型 Skill |

简单请求直接记录当前 Skill；只有组合请求才保存一份很小的阶段状态：

```json
{
  "phases": [
    {"id": "clean", "skill": "data-cleaning", "status": "in_progress"},
    {"id": "generate", "skill": "generate-workflow-dsl", "status": "pending", "after": "clean.succeeded"},
    {"id": "debug", "skill": "debug-workflow", "status": "pending", "after": "generate.validated"}
  ],
  "active_phase": "clean"
}
```

清洗任务回调成功后，系统把 `generate` 激活；生成并校验成功后，再进入 `debug`。如果调试需要一次性授权，则该阶段先等待授权，不应因为它已经在计划中就绕过确认。

这也符合 Codex 的设计：它允许一次任务使用多个 Skill，但要求选择覆盖任务的最小集合并说明顺序。实现上可以复用当前 Skill keyword trigger，增加组合依赖和阶段推进规则。Skill 数量明显增长后，再参考 Codex 的便宜词法召回器缩小候选；不建议现在就引入通用意图分类模型。

#### 第二层：用 JSON 保存真实工作流状态，不保存模型猜测

真正适合结构化保存的是系统已经知道的事实：

```json
{
  "workflow_version": "8f36",
  "lifecycle": "validated",
  "validation": {
    "version": "8f36",
    "status": "passed",
    "retryable": false
  },
  "active_job": null,
  "turn": {
    "workflow_changed": false,
    "blocked_retries": []
  },
  "grants": []
}
```

建议的主状态是：

```text
missing → ready → dirty → validated → debug_running
                               ↑             ↓
                               └── debug_failed / debug_succeeded
```

状态由工具真实结果更新，不由模型填写：

- 画布同步或文件修改后进入 `dirty`，旧校验结果立即失效。
- `validate_workflow_dsl` 对当前版本校验成功后进入 `validated`。
- Debug 提交成功后进入 `debug_running`，平台回调更新终态。
- `retryable=false` 记录到状态中，阻止同一版本的无意义重复调用。

这样程序约束的是“当前状态是否允许这个动作”，而不是先猜“用户属于哪一类任务”。

#### 第三层：高风险动作需要可信授权

普通读取、生成和校验可以让模型自主调用；会产生真实平台任务的动作需要额外授权：

```text
workflow_debug
run_dataset_cleaning
以后可能开放的正式训练
```

授权来源不能是模型自己填写的 JSON，应该来自：

- 用户点击前端“Test/调试”按钮，消息中携带可信的 `requested_operation=debug`。
- 用户明确确认某项高风险操作后，由后端写入一次性 grant。
- 正式训练始终经过独立确认，不因 Prompt 中出现“运行”就自动授权。

例如：

```json
{
  "operation": "debug",
  "workflow_version": "8f36",
  "grant": "workflow_debug_once"
}
```

grant 由后端生成、消费一次后失效，模型只能看见，不能伪造。

#### 第四层：执行前检查确定性规则

当前 OpenHands SDK 已经有 PreToolUse Hook，并且能够在工具执行前读取 `tool_name` 和参数、阻止 Action。应优先复用这个拦截点，而不是另建一套平行 Tool Guard 框架。

建议检查：

| Tool/动作 | 硬规则 |
|---|---|
| `file_editor` / `apply_patch` 写入 | 只能修改工作流目录或明确的自定义资产目录 |
| `validate_workflow_dsl` | `retryable=false` 后，同一版本不允许重复调用 |
| `workflow_debug` | 当前版本已校验；有一次性 debug grant；本轮没有刚修改 DSL |
| `run_dataset_cleaning` | 有 cleaning grant；已经完成数据预览和脚本上传 |
| 正式训练 | 必须有用户确认和对应工作流版本 |

通用的路径、grant 和风险检查放在 PreToolUse；具体业务前置条件也可以在各 Tool Executor 中再次校验。Prompt 和 Skill 负责告诉模型正确路径，执行层保证错误路径不能真正发生。

### 2.4 与 `update_plan` / `task_tracker` 是否冲突

不冲突，但只能保留一个进度工具。

| 数据 | 回答的问题 | 是否有约束力 |
|---|---|---|
| Skill Route | 当前任务需要哪些 Skill、现在执行哪个阶段 | 没有，只负责流程编排和审计 |
| Workflow Lifecycle | 当前工作流处于什么真实状态 | 有，Tool 根据它检查前置条件 |
| `update_plan` | 准备分几步，现在做到哪一步 | 没有，只用于进度展示 |
| `task_tracker` | 另一套任务列表和状态 | 没有，与 `update_plan` 功能重复 |

当前前后端已经接入 `update_plan`，因此建议继续使用它，不再启用 `task_tracker`。工作流状态机不替代计划，计划也不能限制 Tool。

### 2.5 接入当前项目的方式

建议增加两个小模块，并复用现有 Hook：

```text
pyromind/skill_router.py       Skill 匹配、组合依赖、阶段顺序和审计结果
pyromind/workflow_state.py     版本、校验和平台任务生命周期
PreToolUse Hook                统一检查路径、grant 和重复调用
```

接入流程：

1. 用户消息进入 Pyromind Router。
2. Skill Router 识别最小 Skill 集合；简单任务直接激活一个 Skill，组合任务生成有依赖的阶段列表。
3. 运行时只加载当前阶段的 Skill；阶段完成或异步回调成功后，再激活下一个 Skill。
4. 每次 Tool 成功或失败后更新 `WorkflowState`。
5. PreToolUse 在执行前检查工具参数、状态和一次性 grant。
6. 不满足条件时返回结构化拒绝原因，让模型停止、校验或询问用户。
7. 前端计划可以从阶段状态生成；不要再维护一份可能与阶段状态不一致的流程列表。

首期不需要动态重建整套 Agent Tool List。后续工具数量增加时，可以参考 Codex 的 direct/deferred exposure，把少用或高风险 Tool 默认隐藏，需要时再暴露。

### 2.6 分阶段上线

**第一步：收敛 Skill 路由**

- 明确三个 Skill 的匹配条件、组合顺序、阶段完成条件和典型样例。
- 记录规则选择与模型最终选择是否一致。
- 使用固定业务题目校验路由准确率，不增加额外 LLM 调用。

**第二步：建立 WorkflowState 和高风险 grant**

- 文件修改、校验和 Debug Callback 更新真实状态。
- 前端 Test 按钮传递结构化 debug operation。
- Debug、清洗和正式运行使用一次性授权。

**第三步：接入 PreToolUse 硬规则**

- 先限制写入路径、不可重试调用和平台执行 Tool。
- 观察误拦截后，再增加数据预览、上传等前置条件。
- 工具数量增长后再评估 deferred exposure。

这个方案比完整 TaskIntent/Execution Contract 更小：不增加一次分类模型调用，不维护每种任务的工具白名单，也不会把模型猜测当成系统事实。

---

## 三、改造二：保存当前 Workflow 摘要，避免反复完整阅读

### 3.1 当前问题

当前系统提示要求涉及工作流的请求先完整读取：

```text
public_data/workflow_canvas/workflow.py
```

这样能防止模型脱离当前画布工作，但工作流变大以后，每次修改、校验和错误修复都完整阅读，会带来三个问题：

- 重复占用输入 Token。
- 每一轮响应时间变长。
- 模型需要反复从代码中提取数据集、模型、阶段和连线，容易遗漏。

当前代码已经具备两个可以复用的基础：

- `apply_patch` 和 `file_editor` 修改工作流后会标记 workflow dirty。
- EventService 已经会读取完整 DSL，并转换出结构化 `xyflow` 返回前端。

因此不需要再让一个 LLM总结工作流，也不需要维护一份人工编写的第二套事实。

### 3.2 推荐方案：极简概览、按需索引、完整 DSL 三层读取

不应该把所有节点、参数和连线都放进每轮摘要。节点一多，这只是把“反复读取大 Python 文件”换成“反复读取大 JSON”，Token 收益有限。

建议把工作流上下文分成三层：

| 层级 | 内容 | 何时给模型 |
|---|---|---|
| L0：Workflow Overview | 版本、节点数、数据、模型、阶段、校验状态 | 每轮自动注入 |
| L1：Workflow Index | 节点详情、关键参数、上下游和源码行号 | 模型按节点或阶段查询时返回 |
| L2：完整 DSL | 完整 `workflow.py` | 复杂拓扑修改或兜底时读取 |

每轮自动注入的 L0 应保持很小，例如：

```json
{
  "v": "8f36",
  "nodes": 12,
  "stages": ["sft", "benchmark"],
  "data": ["openai/gsm8k"],
  "models": ["Qwen/Qwen3-4B"],
  "validation": "passed"
}
```

建议限制 L0 在 200 Token 以内。字段过多时只保留数量和主要对象，不允许随节点数线性增长。

完整节点索引仍然可以从现有 `xyflow` 确定性生成并保存在后端，但默认不放进 Prompt。Agent 确实需要修改 SFT 节点时，再通过只读的工作流查询接口取得局部结果：

```json
{
  "v": "8f36",
  "node": "sft",
  "type": "SFTTrainingNode",
  "lines": [42, 61],
  "params": {"epochs": 3, "learning_rate": 0.00001},
  "incoming": ["dataset.dataset_path"],
  "outgoing": ["benchmark.model_path"]
}
```

这个局部结果只在需要时进入一次上下文。完整 DSL 仍然是唯一真实来源，L0 和 L1 都只是可丢弃、可重新生成的索引。

### 3.3 更新和失效规则

三层内容必须通过同一个版本避免过期：

1. 对 `workflow.py` 内容计算 hash，作为 `workflow_version`。
2. 创建会话、画布同步到 DSL 后生成摘要。
3. `apply_patch` 或 `file_editor` 修改后，利用现有 dirty 流程重新生成 L0 和 L1。
4. 校验结果只对 `validated_version` 对应的文件有效；文件再次变化后自动变成 `stale`。
5. L0/L1 解析失败或版本不一致时回退到完整读取，不阻断现有功能。

L0 可以保存在 `agent_state`；较大的 L1 保存在会话持久化目录，按需读取。两者都不要放进用户工作流目录。

### 3.4 Agent 什么时候只看摘要，什么时候读全文

建议把当前“所有相关请求都完整读取”改为分级读取：

| 场景 | 读取策略 |
|---|---|
| 询问当前数据、模型、阶段、校验状态 | 只使用 L0 |
| 修改一个明确节点或参数 | 使用 L0，再从 L1 查询这个节点，最后读取对应代码片段 |
| 新增或删除阶段、改变多条连线 | 使用 L0，然后读取完整 DSL |
| 校验现有文件 | 校验工具内部读取文件，模型不必先读全文 |
| 校验返回明确节点错误 | 从 L1 查询节点，再根据行号读取相关片段 |
| L0/L1 与文件版本不一致或解析失败 | 回退到完整 DSL，并重建索引 |

重点不是“永远不读完整文件”，而是只在确实需要全局结构时读取。

### 3.5 为什么不直接让模型写一段文本摘要

LLM 摘要容易出现遗漏和过期，也无法可靠判断摘要对应的是哪个文件版本。确定性概览和索引可以：

- 从同一个 DSL/xyflow 自动生成。
- 用 hash 判断是否过期。
- 让前端、Agent 和校验模块共同使用。
- L0 固定大小，不随工作流节点数无限增长。
- L1 支持通过 node ID 和行号进行局部读取，但不占用每轮 Prompt。

如果需要给用户展示自然语言，可以由前端根据结构化字段拼接，例如：

```text
当前工作流包含 12 个节点：1 个数据入口、SFT、DPO 和 Benchmark；
基座模型为 Qwen/Qwen3-4B；最近一次平台校验已通过。
```

### 3.6 最小实施范围

首期只做以下内容即可：

1. 先实现不超过 200 Token 的 L0，只保存版本、数据、模型、阶段、节点数和校验状态。
2. 每轮只把 L0 注入 Agent 上下文。
3. 修改系统 Prompt 和 `generate-workflow-dsl` Skill 的读取规则。
4. 保留版本不一致时完整读取的兜底。
5. 第二期再增加 L1 节点索引和按节点查询，不阻塞首期收益。

---

## 四、改造三：前端合并展示多个 Tool 调用

### 4.1 当前问题

当前前端已经能使用 `action_id` 把一次 Action 和对应 Observation 配成一张工具卡，但多个工具仍然逐条平铺：

```text
skills_read
skills_read
skills_read
grep
file_editor
apply_patch
validate_workflow_dsl
```

对开发排查来说这些记录有价值，但普通用户更关心：

- 当前处于哪个阶段。
- 读取了什么类型的信息。
- 工作流是否修改成功。
- 校验是否通过，是否有需要处理的异常。

### 4.2 推荐展示结构

默认视图按业务阶段显示：

```text
✓ 已读取 3 份工作流规范
  用于确认：数据字段、节点连接和训练参数
  [查看执行详情]

✓ 已更新工作流
  修改了 4 个节点、3 条连接
  [查看差异]

✕ 工作流校验未通过
  DatasetValidator：字段 gt 不存在
  [定位节点] [查看完整错误]
```

点击“查看执行详情”后再展示原始 Tool 名、参数、Observation 和事件 JSON。底层 EventLog 不删除、不改写，合并只是前端视图。

### 4.3 分组依据

不要按中文文案或模型 Thought 猜测分组。现有事件已经提供了可利用的结构：

- `action_id`：配对 Action 和 Observation。
- `tool_call_id`：标识一次工具调用。
- `llm_response_id`：合并同一次模型响应产生的并行 Tool Call。
- `tool_name`：判断工具类别。
- `summary`：有值时作为补充说明，但不能作为唯一依据。

前端把事件先转换成 `ToolActivity`，再组合成 `ToolActivityGroup`：

```json
{
  "group_id": "turn-12:research:1",
  "category": "research",
  "status": "completed",
  "title": "已读取 3 份工作流规范",
  "summary": "用于确认数据字段、节点连接和训练参数",
  "tool_call_ids": ["call-1", "call-2", "call-3"],
  "has_error": false
}
```

建议的工具类别：

| 类别 | 工具示例 | 默认展示 |
|---|---|---|
| planning | `update_plan` | 独立展示为任务进度，不并入普通工具卡 |
| research | `skills_read`、`grep`、`file_editor:view`、`preview_dataset` | 连续调用合并 |
| editing | `apply_patch`、`file_editor:edit` | 合并为修改摘要，但保留 Diff 入口 |
| validation | `validate_workflow_dsl` | 单独突出结果 |
| execution | `workflow_debug`、数据清洗 | 单独显示任务状态卡 |

### 4.4 合并规则

首期使用简单、可解释的规则：

1. 只合并同一个用户回合里的事件。
2. Action 和 Observation 先按 `action_id` 配对。
3. 同一个 `llm_response_id` 的并行调用合并为一组。
4. 连续、同类别的只读工具可以跨模型响应合并，例如连续三个 `skills_read`。
5. 遇到用户/Agent 正式消息、文件写入、校验或异步任务时结束当前分组。
6. 任意一个调用失败，分组状态变为失败并默认展开错误。
7. 写操作、校验结果和异步任务不能被完全隐藏。

这能覆盖截图中的多个 `skills_read`，又不会把“读取资料、修改文件、平台校验”错误地合成一个步骤。

### 4.5 前后端怎么分工

**第一期只改前端即可：**

- 补齐 `ActionEvent` 的 `tool_call_id`、`llm_response_id` 和 `summary` 类型。
- 在 `ChatPanel` 渲染前增加纯函数 `groupToolActivities(events)`。
- 新增 `ToolActivityGroupBlock`，默认显示摘要，详情中复用现有 `ToolCallBlock`。
- 保留当前事件数组和 WebSocket 协议，不改后端。

**第二期再考虑后端业务摘要：**

- 如果不同前端都需要一致展示，可以由后端投影出 `tool_activity_group`。
- 后端只提供结构化分组信息，原始事件仍然保留。

第一期不建议新增一套 Event 类型，避免为了 UI 展示扩大协议改动。

---

## 五、三项改造如何配合

以“把当前 SFT 工作流换成 Qwen3-4B 并校验”为例：

### 改造前

1. 模型根据 Prompt 猜应该调用哪个 Skill。
2. 完整读取 `workflow.py`。
3. 读取多份 Reference。
4. 修改文件并校验。
5. 前端逐条展示所有 Tool Call。

### 改造后

1. Skill 路由规则识别到“修改优先于调试”，选择 `generate-workflow-dsl`。
2. WorkflowState 记录当前版本和校验状态；本轮没有 Debug grant。
3. Agent 先读取 Workflow Digest，定位模型节点；需要时只读对应片段。
4. PreToolUse 检查 Patch 只修改工作流路径；若模型误调 Debug，因无 grant 被阻止。
5. 文件修改后自动更新 Digest，旧校验结果失效。
6. 调用现有 `validate_workflow_dsl`。
7. 前端显示三张卡：任务说明、工作流修改、校验结果；资料读取折叠为一行。

最终用户看到的是：

```text
任务：修改当前工作流并校验
边界：不会启动调试或正式训练

✓ 已确认当前工作流：SFT + Benchmark
✓ 已将模型修改为 Qwen/Qwen3-4B
✓ 平台校验通过
```

---

## 六、实施计划

### 第一阶段：一到两个迭代，先拿到确定收益

1. 增加 Workflow Digest 的最小字段和 hash 失效机制。
2. 调整 Prompt/Skill：简单查询使用摘要，复杂修改才读全文。
3. 前端实现只读工具分组和详情折叠。
4. 增加埋点，记录完整读取次数、输入 Token、工具卡数量和执行耗时。

这一阶段不改变工具权限，兼容风险较低。

### 第二阶段：收敛 Skill 路由并建立 WorkflowState

1. 给现有 Skill 增加匹配条件、组合依赖、阶段完成条件和路由测试样例。
2. 保存工作流版本、dirty、validation 和 active job 状态。
3. 前端 Test 按钮携带可信的 debug operation，并生成一次性 grant。
4. 用 SFT、DPO、GRPO、Benchmark、修改和调试请求建立路由评测集。

### 第三阶段：开启关键硬边界

1. 通过 PreToolUse 限制可写路径和未经授权的平台任务。
2. 在 Tool Executor 中检查版本、校验状态和业务前置条件。
3. 对不可重试错误阻止重复调用。
4. 根据观察数据逐步增加其他确定性规则。

---

## 七、验收指标

以下是建议目标，需要先采集当前基线再确认最终数值：

| 指标 | 建议目标 |
|---|---|
| Skill 路由准确率 | 固定评测集达到 95% 以上 |
| 路由额外 LLM 调用 | 0 |
| 高风险越界调用 | 开启强制模式后为 0 |
| 每轮完整读取 workflow.py 次数 | 下降 60% 以上 |
| 工作流相关请求输入 Token | 下降 30% 以上 |
| 平均工具卡数量 | 下降 50% 以上 |
| 原始工具事件可追溯率 | 100%，合并展示不丢事件 |
| 摘要过期导致错误修改 | 0，hash 不一致必须回退全文 |

除了效率指标，还要观察工作流生成和修改的正确率，不能为了少读文件而降低 DSL 质量。

---

## 八、风险和处理方式

| 风险 | 处理方式 |
|---|---|
| Skill 路由遗漏组合任务 | 路由输出最小 Skill 集合和顺序；运行时一次只激活一个阶段 |
| Workflow Digest 过期 | 内容 hash + validated version；不一致自动读全文 |
| 摘要遗漏细节导致错误修改 | 复杂拓扑修改仍读全文；摘要不是 DSL 的替代品 |
| 工具分组掩盖错误 | 失败组默认展开；校验、写操作和异步任务始终保留独立入口 |
| 前后端协议改动过大 | 第一期完全基于现有 Event 字段在前端分组 |
| 与 `update_plan` 重复 | WorkflowState 管真实状态、Digest 管上下文、Plan 管进度，三者不混用 |

---

## 九、周会上可以直接这样汇报

### 两分钟版本

> 我这次主要想提三个改造点。第一，现在 Skill 和工具选择主要依赖 Prompt，模型理解错了就可能走错流程。我对比了 Codex，它也没有先做一套完整任务分类 JSON，而是让 Skill 描述负责语义选择，把硬限制放在 Tool Router、PreToolUse、沙箱和审批层。因此我们的方案可以更轻：Router 识别完成任务所需的最小 Skill 集合和顺序，运行时一次只激活一个阶段；用 WorkflowState 保存文件版本、校验和任务状态；在现有 PreToolUse 和具体 Tool 中检查写入路径、不可重试调用和平台任务授权。这样既支持“清洗、生成、调试”这类组合任务，也不会一次加载多个 Skill 造成规则冲突。
>
> 第二，现在处理工作流时经常反复完整读取 workflow.py，文件大以后会增加 Token 和响应时间。当前后端已经能把 DSL 转成 xyflow，也有 workflow dirty 标记，所以可以直接从现有结构生成一份带版本 hash 的工作流摘要，保存节点、关键参数、连线、数据、模型和校验状态。普通查询和局部修改先用摘要，复杂结构修改才读全文；hash 不一致就自动回退，不牺牲正确率。
>
> 第三，前端现在把每个 skills_read、grep、file_editor 都单独展示，用户看到的是底层流水账。现有事件已经有 action_id、tool_call_id 和 llm_response_id，前端可以在不改后端协议的情况下，把同一阶段的只读调用合并成一张卡，原始 JSON 放到执行详情里；修改、校验、异步任务和错误继续单独突出。
>
> 三项合起来就是：Skill 路由负责确定最小 Skill 集合和阶段顺序，WorkflowState 和 PreToolUse 管边界，Workflow Digest 管上下文，工具分组管展示。建议先做摘要和前端分组，再补真实状态和关键硬限制。

### 如果被问“为什么不用 task_tracker”

> `task_tracker` 或现在的 `update_plan` 只记录有几步、完成到哪一步，不能限制模型调用什么工具。我们已经接入 `update_plan`，不需要再启用一套重复的任务列表。WorkflowState 和 PreToolUse 负责真实状态和执行边界，两者职责不同。

### 如果被问“为什么一定要 JSON”

> 不是把自然语言任务完整翻译成 JSON。JSON 只保存程序已经确认的事实，例如 workflow 版本、是否修改、校验结果、活跃任务和一次性授权；Skill 选择仍然由轻量规则和模型完成。

### 如果被问“摘要会不会让模型看不全”

> 摘要不是替代 workflow.py。它用于问答和局部修改，复杂拓扑变化仍然读取全文；同时通过文件 hash 判断摘要是否过期，一旦不一致就回退到完整读取。

### 如果被问“合并后还能不能排查问题”

> 可以。原始 EventLog、Tool 参数和 Observation 都保留，只是默认折叠。失败、写操作、校验和异步任务不会被隐藏，开发人员仍然可以展开查看完整 JSON。

---

## 十、最终建议

这三点都值得做，但不要同时做成一个很大的新框架。

- **Workflow Digest**复用现有 `xyflow` 和 dirty 流程，先落地。
- **前端工具分组**基于现有事件字段实现，不改协议，先改善体验。
- **Skill 路由与 WorkflowState**先覆盖确定性规则，再通过 PreToolUse 约束高风险动作。

这样改动范围可控，不破坏已有 Prompt、Skill、Tool 和会话能力，也能分别量化稳定性、Token、耗时和前端可读性的提升。
