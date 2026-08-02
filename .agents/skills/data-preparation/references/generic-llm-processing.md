# 通用 LLM 生成、改写与过滤

## 适用

为单字段生成内容、多字段组合评分、文本改写或生成后质量过滤。最小输入是一个或多个
字符串列；最终输出 `text`。

## 算子选择

- 单字段生成：`PromptedGenerator`
- 多字段模板：`FormatStrPromptedGenerator + FormatStrPrompt`
- 原地改写：`PromptedRefiner`
- LLM 分数过滤：`PromptedFilter`
- 已有分数或规则过滤：`GeneralFilter`
- 生成结构化 JSON 时给 PromptedGenerator 提供严格 JSON Schema

```python
generator = PromptedGenerator(llm_serving=llm, system_prompt=SYSTEM_PROMPT)
generator.run(storage=storage, input_key="prompt", output_key="answer")
storage = storage.step()

quality = PromptedFilter(
    llm_serving=llm,
    system_prompt="从正确性、完整性和清晰度评分 1-5。",
    min_score=4,
    max_score=5,
)
quality.run(storage=storage, input_key="answer", output_key="quality_score")
storage = storage.step()
```

Pipeline 最后只输出 `id/system_prompt/user_prompt/gt`。多字段输入必须使用格式模板，
不要串联多个职责相同的 generator。`PromptedRefiner` 前先过滤空值。
