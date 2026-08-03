# 目标格式

正式训练产物统一为 UTF-8 JSONL，一行一个对象，不允许额外字段。审计、源路径、模型
和重试信息写入 Report，不写进训练行。

## 文本

```json
{
  "id": "alpaca-gpt4-2",
  "system_prompt": "You are a helpful assistant.",
  "user_prompt": "用户问题",
  "gt": "标准答案"
}
```

- 四个字段都必须是非空字符串。
- `id` 在整个文件内唯一。

## DPO

```json
{
  "id": "alpaca-gpt4-2",
  "system_prompt": "You are a helpful assistant.",
  "user_prompt": "用户问题",
  "gt": "推荐回答",
  "rejected_answer": "不推荐回答"
}
```

- 五个字段都必须是非空字符串。
- `gt` 是 chosen / preferred answer；`rejected_answer` 是 rejected answer。
- `gt` 和 `rejected_answer` 去除首尾空白后不能相同。
- `id` 在整个文件内唯一。

## 图片

```json
{
  "id": "geo-training-0",
  "image_path": "images/geo-training-000000.png",
  "images": ["images/geo-training-000000.png"],
  "system_prompt": "You are a helpful assistant.",
  "user_prompt": "用户问题",
  "gt": "<think>推理</think>\n\n<answer>A</answer>"
}
```

- `images` 是非空、有序的相对 POSIX 路径列表。
- `image_path` 必须等于 `images[0]`。
- 路径不允许绝对路径或 `..`。
- `gt` 只能包含一组非空 `<think>` 和 `<answer>`；是否限制 A/B/C/D 由任务决定。

Pipeline 直接写出上述格式，并通过 `df_run_pipeline(output_schema=...)` 或
`df_submit_pipeline(output_schema=...)` 自动校验。

## 旧格式兼容

已有 `messages`、`preference`、`trl_vision_sft` 和 `vision_sft_flat` 的 `df_convert`
调用继续可用，但不属于新的标准链路。
