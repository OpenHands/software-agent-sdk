# 目标格式

## messages 格式（用于 SFT/通用对话）

```json
{"messages": [
  {"role": "system", "content": "optional system prompt"},
  {"role": "user", "content": "the problem"},
  {"role": "assistant", "content": "reasoning\n\nanswer"}
]}
```

用 `df_convert --format messages --text_field problem --reasoning_field cot --answer_field answer [--system_prompt "..."]`

## preference 格式（用于 DPO/RLHF）

```json
{"prompt": "the problem", "chosen": "chosen response", "rejected": "rejected response"}
```

用 `df_convert --format preference --text_field prompt --chosen_field chosen --rejected_field rejected`

## 扁平视觉 SFT（当前业务格式）

输入 `processed.jsonl` 至少包含：

```json
{
  "sample_id": "sample-001",
  "training_system_prompt": "任务级系统提示",
  "training_prompt": "比较这些图片并给出结论",
  "training_response": "<think>推理</think>\n\n<answer>A</answer>",
  "image_paths": ["images/a.jpg", "images/b.jpg"]
}
```

调用：

```text
df_convert(
  format="vision_sft_flat",
  input_path="processed.jsonl",
  output_path="train.parquet",
  id_field="sample_id",
  system_prompt_field="training_system_prompt",
  prompt_field="training_prompt",
  response_field="training_response",
  images_field="image_paths"
)
```

输出列固定为
`id/image_path/images/system_prompt/user_prompt/gt`。`images` 保存所有原始路径及
顺序，`image_path` 是首图兼容别名，不嵌入 PIL、bytes 或 base64。转换前验证图片存在
且可解码，`gt` 必须是完整的 `<think>/<answer>` 响应。

## TRL vision SFT（兼容格式）

旧调用 `format="trl_vision_sft"` 保持 `messages + 嵌入式 images` 语义，供已有流程使用。
