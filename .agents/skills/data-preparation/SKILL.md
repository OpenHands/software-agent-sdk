---
name: data-preparation
description: >-
  使用 DataFlow 对文本或图片数据进行抽样、清洗、生成、评分和格式化，覆盖 SFT、
  推理、代码、知识问答、Agentic RAG、多轮对话、Function Call、质量评估、
  Text2SQL、化学抽取和多模态标注；本地验证后提交 Pyromind 全量处理。
---

# 数据准备

先在本地验证最多 3 条 Sample；用户明确确认后，才提交 Pyromind 全量任务。

## 强制边界

- 工作区中间文件放在 `public_data/data-preparation/`。
- Storage 数据和平台产物只能用 `preview_dataset` 查看，不得用本地文件工具读取。
- `df_run_pipeline` 只运行本地 Sample；用户确认前不得调用 `df_submit_pipeline`。
- 新链路直接生成规范 JSONL，不以 `df_convert` 或 Parquet 作为正式产物。
- 使用 LLM 的 DataFlow 算子必须由 `LoggingLLMServing` 包装。

## 执行流程

1. `preview_dataset(mode="inspect")` 确认结构，再用 `mode="sample"` 物化最多 3 条。
2. 根据下表只读取相关场景 reference，同时读取
   [通用约定](references/dataflow-common.md) 和
   [输出契约](references/schema-conventions.md)。
3. 从 [文本模板](references/text_pipeline.py) 或
   [图片模板](references/multimodal_pipeline.py) 修改 Pipeline；场景 reference
   中的算子链负责处理中间字段，Pipeline 末尾负责映射正式 Schema。
4. 调用 `df_run_pipeline`，显式设置 `model_profile` 和 `output_schema`，检查
   `processed.sample.jsonl`、`validation.json` 和 `report.json`。
5. 展示 Sample 结果并等待用户明确确认。
6. 调用 `df_submit_pipeline(mode="full")`。收到 Kafka callback 后，调用
   `preview_dataset` 查看 `<output_dir>/report.json`；如失败，再查看同目录的
   `failure.json`、`validation.json` 和必要的 `llm_calls.jsonl`。
7. Agent 修复后先在本地重跑失败记录、失败前一条和同类成功记录：
   - 旧结果仍可用：`mode="resume"`，提交 `reuse_assessment` 和可选新脚本。
   - 旧结果不可用：重新执行 Sample、人工确认并创建新的 full run。
8. 提交后可用 `df_check_progress`（传 `output_dir`）查看实时进度、ETA 和最近产出。
   若用户预览后发现不符合预期、要介入调整，**先调用 `df_stop_task`**（传 `task_id`，
   或 `df_submit_pipeline` 返回的 `run_id` / `output_dir`）停掉平台任务，再修改
   pipeline 并重新提交，避免旧任务继续消耗资源或覆盖输出目录。
## 运行规则

| 需求 | Reference | `output_schema` |
|---|---|---|
| 规则清洗、语言/长度过滤、去重 | [文本规则清洗](references/text-cleaning.md) | 下游 Schema |
| 通用生成、改写、打分、过滤 | [通用 LLM 处理](references/generic-llm-processing.md) | `text` |
| SFT 合成与筛选 | [SFT 数据](references/sft-data.md) | `text` |
| 推理问题和答案合成 | [Reasoning 数据](references/reasoning-data.md) | `text` |
| 代码指令和代码生成 | [Code 数据](references/code-data.md) | `text` |
| 文本/Markdown 清洗并生成 QA | [知识库与 QA](references/knowledge-qa.md) | `text` |
| Agentic RAG 任务与 QA | [Agentic RAG](references/agentic-rag.md) | `text` |
| 多轮对话生成或整理 | [多轮对话](references/multiturn-data.md) | `multiturn` |
| 工具定义和调用轨迹 | [Function Call](references/function-call-data.md) | `function_call` |
| 样本质量评分、保留、改写或丢弃 | [质量评估](references/quality-evaluation.md) | `quality_evaluation` |
| 已有 SQLite Text2SQL 数据精炼 | [Text2SQL](references/text2sql-data.md) | `text2sql` |
| 图片 OCR、理解和多图语义标注 | [多模态标注](references/multimodal-labeling.md) | `vision` |
| 从文本抽取 SMILES | [化学数据](references/chemistry-data.md) | `text` |

## 运行与完成条件

- 文本任务使用 `model_profile="text"`；图片任务使用 `model_profile="vision"`。
- 图片 Pipeline 只配置 `ImagePipelineConfig`，不得自行实现 HTTP、Base64、重试或
  Checkpoint。
- Text2SQL Sample 使用 Python 3.10 的 `DATAFLOW_PYTHON`；Pyromind 固定
  `open-dataflow==1.0.10`、CPU 执行。
- `processed.jsonl` 必须通过所选 Schema 校验，ID 唯一且不含运行审计字段。
- Report 必须包含输出数、模型调用、失败、Checkpoint、校验、Revision，以及场景
  Pipeline 提供的可选 `scenario_metrics.json`。

## 暂不使用

- PDF/MinerU、PDF-VQA、Speech、FlashRAG/Retriever、GPU/本地模型算子。
- 需要独立 Embedding Serving 的完整 Text2SQL 合成。
- `KBCChunkGenerator`、RDKit SMILES 等价评估、默认 CUDA 的质量过滤器。
- `CodeSandboxSampleEvaluator`；不得执行生成代码。

## 图片补充参考

- [image_utils API](references/image-utils-api.md)
- [AVI Manifest 适配器](references/avi_manifest_adapter.py)
