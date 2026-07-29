# DataFlow 算子与脚本约定

## 基础约定

使用 `LazyFileStorage`，每个算子执行后必须 `storage = storage.step()`，再让下一个
算子读取新字段或读取最终结果：

```python
storage = LazyFileStorage(input_path, cache_type="jsonl")
storage = storage.step()
operator.run(storage=storage, ...)
storage = storage.step()
data = storage.read(output_type="dict")
```

`df_run_pipeline` 注入凭证和模型配置。文本 serving 使用完整 endpoint
`DF_API_URL`；VLM serving 使用 OpenAI-compatible 根地址 `DF_API_BASE_URL`；
模型名使用 `DF_MODEL_NAME`，密钥环境变量名为 `DF_API_KEY`。脚本不得读取、打印或
硬编码密钥。

DataFlow serving 包可能引入不需要的重依赖。文本 pipeline 优先复制并修改
[example_pipeline.py](example_pipeline.py) 中的 importlib shim，不要直接从
`dataflow.serving` 导入。

## 核心文本算子

### 单字段生成

```python
generator = PromptedGenerator(
    llm_serving=llm,
    system_prompt="...",
    user_prompt="",
    json_schema=None,
)
generator.run(storage=storage, input_key="text", output_key="generated")
```

`input_key` 和 `output_key` 属于 `run()`；`run()` 返回字段名，不返回 storage。

### 多字段生成或评分

```python
prompt = FormatStrPrompt(
    f_str_template="问题: {question}\n答案: {answer}\n只返回 1-5 分。"
)
operator = FormatStrPromptedGenerator(
    llm_serving=llm,
    system_prompt="...",
    prompt_template=prompt,
)
operator.run(
    storage=storage,
    output_key="score",
    question="problem",
    answer="solution",
)
```

关键字参数的名称匹配模板占位符，值是现有 DataFrame 列名。

### 过滤与改写

- `PromptedFilter.run(storage, input_key, output_key)`：单字段 LLM 语义评分过滤。
- `GeneralFilter([lambda df: ...]).run(storage)`：已有字段的确定性过滤。
- `PromptedRefiner.run(storage, input_key)`：原地覆盖字段；调用前过滤空值。
- `Text2MultiHopQAGenerator.run(storage, input_key, output_key)`：输出嵌套 QA 列表。

长度、语言、去重、PII、emoji、HTML 和空格处理使用 `general_text` 中对应 Filter
或 Refiner，不调用 LLM。

## 结构化输出

每个 JSON Schema 中 `"type": "object"` 的节点都设置
`"additionalProperties": false`。在 pipeline 内解析并校验模型返回值，把单样本
失败写入报告，不因一条坏数据丢弃整个批次。
