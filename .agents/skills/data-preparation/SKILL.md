---
name: data-preparation
description: >-
  对文本或图片数据进行抽样检查、本地 Pipeline 试跑、人工确认和 Pyromind 全量
  处理，生成规范训练 JSONL；支持图片 OCR/语义打标、模型重试、失败报告和断点续跑。
---

# 数据准备

先在本地验证最多 3 条 Sample；用户明确确认后，才提交 Pyromind 全量任务。

## 强制边界

- 工作区中间文件放在 `public_data/data-preparation/`。
- Storage 数据和平台产物只能用 `preview_dataset` 查看。不得用 Terminal、
  `file_editor`、本地 `Path` 或下载脚本读取 Pyromind 路径。
- `preview_dataset(mode="inspect")` 浏览 Storage 文件和文件夹；
  `mode="sample"` 将 Agent 选定的最多 3 个文件或文件夹物化到工作区，并返回
  `sample_manifest_path`。图片由该 Tool 调用 Gemma 返回 OCR 和视觉摘要。
- `df_run_pipeline` 仅运行本地 Sample；用户确认前不得调用 `df_submit_pipeline`。
- 新链路直接生成规范 JSONL，不以 `df_convert` 或 Parquet 作为正式产物。

## 执行流程

1. 调用 `preview_dataset(mode="inspect")` 确认数据结构；目录含多个条目时，自主选择
   能覆盖正常和边界情况的 3 条。
2. 调用 `preview_dataset(mode="sample", sample_paths=[...])` 下载 Sample。图片任务
   同时查看 `vision_previews`，用其 OCR/摘要理解数据，不把原图传给主编程模型。
3. 编写 Pipeline。文本和图片输出分别遵循
   [目标格式](references/target-formats.md)；图片 Pipeline 从
   [配置模板](references/multimodal_pipeline.py) 修改，只填写
   `ImagePipelineConfig`，不要生成循环、HTTP、Base64、重试或 Checkpoint。
4. 调用 `df_run_pipeline`，显式设置 `model_profile` 和 `output_schema`，检查
   `processed.sample.jsonl` 及本地 Report。
5. 展示 Sample 结果并等待用户明确确认。
6. 调用 `df_submit_pipeline(mode="full")`。收到 Kafka callback 后，调用
   `preview_dataset` 查看 `<output_dir>/report.json`；如失败，再查看同目录的
   `failure.json`、`validation.json` 和必要的 `llm_calls.jsonl`。
7. Agent 修复后先在本地重跑失败记录、失败前一条和同类成功记录：
   - 旧结果仍可用：`mode="resume"`，提交 `reuse_assessment` 和可选新脚本。
   - 旧结果不可用：重新执行 Sample、人工确认并创建新的 full run。

## 运行规则

- 图片任务使用 `model_profile="vision"`，由服务端 `DF_*` 配置选择 Gemma；文本任务
  使用 `model_profile="text"`，沿用主对话模型。
- 文本 Pipeline 使用自动投递的 `preparation_runtime.py`；图片 Pipeline 使用
  `image_utils.py` 封装的 DataFlow 多图算子。模型瞬时或输出校验错误最多重试 3 次；
  永久错误或重试耗尽时停止任务，保留 DataFlow Checkpoint 和 `failure.json`。
- 断点续跑复用同一输入 Manifest 和已提交 JSONL 前缀。脚本、Prompt、模型或 Schema
  变化不是机械拒绝条件，但 Agent 必须提交包含变更、验证样本和复用理由的
  `reuse_assessment`。
- 平台每次恢复产生新的 execution revision，Report 必须能追溯该段输出使用的版本。

## 完成条件

- `processed.jsonl` 通过 `validate_prepared_data.py`，ID 唯一且没有额外审计字段。
- 文本行固定为 `id/system_prompt/user_prompt/gt`。
- 图片行固定为 `id/image_path/images/system_prompt/user_prompt/gt`，
  `image_path == images[0]`，`gt` 为 `<think>...</think>` 后接
  `<answer>...</answer>`。
- Report 明确记录状态、输出数、模型调用、失败信息、Checkpoint、校验结果和 Revision。

## 按需参考

- [目标格式](references/target-formats.md)
- [通用文本 Pipeline](references/text_pipeline.py)
- [通用图片 Pipeline](references/multimodal_pipeline.py)
- [image_utils API](references/image-utils-api.md)
- [多图语义打标说明](references/multimodal-labeling.md)
