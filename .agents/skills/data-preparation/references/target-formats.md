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
