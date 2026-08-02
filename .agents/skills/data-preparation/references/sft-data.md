# SFT 数据合成与筛选

## 适用

从种子文本或领域内容生成 instruction/response，并对可读性、相关性和回答质量筛选。
最终输出 `text`。

## 推荐链路

```python
CondorGenerator(llm_serving=llm).run(
    storage=storage,
    input_key="text",
    output_instruction_key="instruction",
    output_response_key="response",
)
storage = storage.step()

CondorRefiner(llm_serving=llm).run(
    storage=storage,
    input_instruction_key="instruction",
    input_response_key="response",
)
storage = storage.step()

AlpagasusFilter(llm_serving=llm).run(
    storage=storage,
    input_instruction_key="instruction",
    input_response_key="response",
)
storage = storage.step()
```

具体 `run()` 参数以 DataFlow 1.0.10 算子签名为准；Agent 编写 Pipeline 前应在当前
解释器中检查签名。最终映射：

```text
instruction → user_prompt
response    → gt
```

不使用默认 CUDA 的 `DeitaQualityFilter`、`SuperfilteringFilter`。
