---
name: data-preparation
description: >-
  用 DataFlow 对本地或 HuggingFace 数据做内容级准备，包括规则清洗、过滤、去重、
  LLM/VLM 生成与评分、单图或多图语义打标、参考标注补全，以及 messages、
  preference 或 TRL 视觉 SFT 格式转换。用于数据质量治理、训练数据生成、图片语义
  标注和需要可抽样、可重跑、可审计的数据处理任务。
---

# 数据准备

在本地会话工作区试跑，用户确认后将全量任务提交到 Pyromind 平台异步执行。

**强制约束：**
- 所有中间文件写入 `public_data/data-preparation/`。
- `file_editor` 和 `terminal` 使用 conversation workspace 的绝对路径；工具参数
  （`df_run_pipeline`、`dataset_download` 等）使用 workspace 相对路径。不要依赖
  terminal cwd，不要 `cd` 到相对路径。
- 样本数据直接用 `file_editor` 创建文件，不要写 `make_sample.py` 之类的中间脚本
  再用 terminal 执行。
- 不要修改项目源码或 skill 文件，也不要用终端探测依赖；`df_run_pipeline` 会检查
  运行环境。
- 本文档的流程和决策规则已自包含。仅在编写 pipeline 需要算子签名或脚本模板时才
  读取 `references/` 下的文件，不要为了理解流程而读取。

## 核心流程

`原始数据 → manifest.jsonl → processed.jsonl → 训练文件`

- 检查用户目标、样本、字段和相关规则，形成字段策略与验证方式。
- HuggingFace 数据先用 `dataset_download` 下载少量样本，确认 split、config 和字段；
  `output_path` 使用 `public_data/data-preparation/` 下的 workspace 相对路径。
- 输入适配、内容处理和格式转换保持为可独立重跑的阶段。
- 默认首次抽样最多 3 条，写入 `processed.sample.jsonl`；这只是模型行为提示，
  不是运行时门控。用户明确要求全量时可直接生成 `processed.jsonl`。
- 使用 DataFlow LLM 的 pipeline 必须用 `LoggingLLMServing` 包装 serving；
  `df_run_pipeline` 自动把 `df_logging.py` 和 `generate_report.py` 投递到脚本目录。
- 试跑成功后**必须主动**向用户展示样本结果并询问：“试跑结果符合预期吗？确认后
  我将提交平台执行全量数据。”不要跳过此步骤，即使结果看起来正确也必须等用户
  明确确认后才能提交。
- 确认后调用 `df_submit_pipeline`（传本地 `script_path` 和 Storage `input_path`）；
  工具内部自动生成 run_id、上传 pipeline 和 runtime 文件到 Storage，无需手动调
  `upload_file_to_pyromind`。收到 callback 后依次检查 `report.json`、异常时的
  `llm_calls.jsonl`，以及 `processed.jsonl`。
- 处理完成后调用 `df_convert`，并重新加载最终训练文件验证；已在平台生成的产物无需重复上传。

## 决策规则

- 下游字段必须来自原始数据或更早阶段。
- 默认保留输入字段；需要修订时生成新字段，或遵循用户明确要求。
- 根据用户目标、实际字段和相关规则自主决定模型输入字段、生成字段、保留字段与训练响应；
  不固定图片数量或业务字段名。
- 规则只提炼直接影响判断的部分，不把整份文档塞进 prompt。
- 确定性清洗优先使用 `general_text` 算子。
- 单字段生成使用 `PromptedGenerator`；多字段 prompt 使用
  `FormatStrPromptedGenerator`；LLM 语义过滤使用 `PromptedFilter`。
- QA 生成优先 `Text2MultiHopQAGenerator`；文本改写使用
  `PromptedRefiner`。
- 视觉内容由 DataFlow VLM serving 读取；主 Agent 只编写和调度脚本，并接收文本摘要、
  路径与状态。
- 单图和多图语义任务复用现有 VLM serving，不新增 DataFlow 核心算子。

需要算子签名、storage step 约束或 serving 导入方式时，读取
[算子与脚本约定](references/operators.md)。需要常见文本流水线组合时，读取
[流水线模式](references/patterns.md)。不要为多模态任务加载这些文本示例，除非任务
同时包含文本算子。

## 多模态任务

多模态任务只读取：

- [多图语义打标](references/multimodal-labeling.md)
- [通用多图 pipeline 模板](references/multimodal_pipeline.py)
- [目标格式](references/target-formats.md)

Agent 根据用户目标、数据和规则自主选择：

- 哪些原始或参考字段可供模型读取；
- 生成哪些字段；
- 哪些字段原样保留；
- 推理字段、答案字段以及训练 prompt 如何组装。

VLM 按任务 JSON Schema 返回结构化字段；本地代码从任务选定的推理字段和答案字段组装：

```text
<think>...</think>

<answer>...</answer>
```

不要硬编码 `label`、`note`、`cot` 或 AVI 语义。最终训练 user prompt 不得泄漏只在
生成阶段使用的参考答案。

## 完成条件

- 样本和全量文件名明确区分，抽样结果不能冒充全量产物。
- 处理报告包含读取、成功、失败数量、失败样本和字段策略。
- 文本 SFT/DPO 分别输出 `messages.jsonl`/`preference.jsonl`。
- 视觉 SFT 的 `processed.jsonl` 包含任务系统提示、样本 prompt、结构化标注、
  已组装的标签响应和有序图片路径。
- `vision_sft_flat` 输出 `id/image_path/images/system_prompt/user_prompt/gt`；
  图片保持路径形式和原始顺序。
- `df_convert` 的 converted 数量等于成功标注数量；任何 skipped 或失败都在交付中说明。
- 视觉 Parquet 重新加载后列、行数、路径和图片可解码性均通过验证。

## 参考

- [算子与脚本约定](references/operators.md)
- [文本流水线模式](references/patterns.md)
- [目标格式](references/target-formats.md)
- [多图语义打标](references/multimodal-labeling.md)
- [通用多图 pipeline 模板](references/multimodal_pipeline.py)
- [AVI manifest adapter 示例](references/avi_manifest_adapter.py)
