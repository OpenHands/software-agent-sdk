# 图片语义打标

图片 Pipeline 只声明 `ImagePipelineConfig`，不要生成循环、HTTP、OpenAI Client、
Base64、重试或 Checkpoint。完整字段见
[image_utils API](image-utils-api.md)，起始脚本见
[配置模板](multimodal_pipeline.py)。

## 输入

图片原始数据集格式不可预设。先用 `preview_dataset(mode="inspect")` 的
`directory_summary` 判断结构：

- `detected_layout` 只是摘要里的弱信号；查看实际文件和 metadata 后再决定
  Prompt/字段映射。
- `flat_file_collection`、`mixed_directory`、`unknown` 或低置信度结构：继续 inspect
  具体文件或向用户确认样本边界，不要假设每个目录就是一条样本。
- 如需代表性、类别覆盖、异常覆盖、大小覆盖或命名模式覆盖，应基于
  `directory_summary` 继续 inspect 或自行选择 `sample_paths`。
- layout hint 只是弱信号，不是 Schema；Pipeline 仍以 sample 后看到的真实字段为准。

本地验证优先使用 `preview_dataset(mode="sample")` 返回的 Manifest。默认字段：

```json
{
  "id": "sample-0",
  "images": ["sample-a/front.png", "sample-a/back.png"],
  "image_labels": ["front", "back"],
  "user_prompt": "判断两张图片是否描述同一对象"
}
```

- 支持 `id/images`，并兼容 `sample_id/image_paths/prompt`。
- 图片路径必须相对于输入目录或 Manifest，保持原顺序。
- 每条记录优先读取 `user_prompt_key`；字段为空时才渲染
  `user_prompt_template`。两者都无法生成 Prompt 时失败。
- `image_labels` 可省略；运行时生成 `Image 1`、`Image 2`。

目录输入按直接子目录或直接图片划分样本，并生成冻结的
`source_manifest.jsonl`；目录样本通常需要配置 `user_prompt_template`。

## 执行与恢复

运行时通过 DataFlow `APIVLMServing_openai` 完成多图请求，通过单个组合 Operator
生成最终 Pyromind 行。结构化结果失败最多重试 3 次，任一记录最终失败时停止当前
batch，不跳过数据。

DataFlow checkpoint 决定已提交 batch；输出先写原子分片，再合并为
`processed.jsonl`。resume 复用原 Manifest 和已提交分片，不重新扫描输入目录。

本地使用：

```text
df_run_pipeline(model_profile="vision", output_schema="vision")
```

用户确认后使用 `df_submit_pipeline(mode="full")`。平台产物仍只能在 Kafka callback
后通过 `preview_dataset` 查看。

## 输出

```json
{
  "id": "sample-0",
  "image_path": "sample-a/front.png",
  "images": ["sample-a/front.png", "sample-a/back.png"],
  "system_prompt": "You are a helpful assistant.",
  "user_prompt": "判断两张图片是否描述同一对象",
  "gt": "<think>推理</think>\n\n<answer>答案</answer>"
}
```

`validate_prepared_data.py` 继续执行最终字段、ID、图片路径、图片解码和标签校验。
