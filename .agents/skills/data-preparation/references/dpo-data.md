# DPO 数据清洗与生成

## 适用

输出 Pyromind DPO 标准 JSONL：每行包含 `id`、`system_prompt`、`user_prompt`、
`gt` 和 `rejected_answer`。审计、分数、生成策略和过滤原因只写入 Report 或
`scenario_metrics.json`。

DPO Pipeline 优先使用 DataFlow 算子作为主流程；生成、过滤、打标、去重等已有算子
能覆盖的环节，建议复用算子而不是手写重复逻辑。普通 Python 可用于字段别名归一、
解析算子输出、补充业务校验和最终 Schema 写出。

支持两类输入：

- 已有偏好对：源数据已有 chosen/rejected、preferred/rejected、accept/reject 等字段。
- 只有输入：源数据只有 `input`、`prompt`、`question` 或 `user_prompt`，需要生成正负两个回答。

## 已有偏好对清洗

字段映射在 Pipeline 末尾完成，常见别名默认映射为：

```text
prompt/question/input/user_prompt -> user_prompt
chosen/preferred/gt/response      -> gt
rejected/rejected_answer/bad      -> rejected_answer
system/system_prompt              -> system_prompt
```

推荐链路：

```python
for key in ["user_prompt", "gt", "rejected_answer"]:
    ContentNullFilter().run(storage=storage, input_key=key)
    storage = storage.step()

GeneralFilter([
    lambda df: df["gt"].str.strip() != df["rejected_answer"].str.strip(),
]).run(storage=storage)
storage = storage.step()

HashDeduplicateFilter(hash_func="md5").run(
    storage=storage,
    input_keys=["system_prompt", "user_prompt", "gt", "rejected_answer"],
)
storage = storage.step()
```

`system_prompt` 缺失时填 `"You are a helpful assistant."`。不要默认清除 URL、HTML、emoji
或压缩答案换行；只有 Sample 明确显示这些是噪声时，才对指定字段追加 Refiner。

## 只有输入时生成偏好对

优先用 `FormatStrPromptedGenerator` 一次生成结构化对象，减少 chosen/rejected 不匹配：

```python
schema = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "chosen": {"type": "string"},
        "rejected": {"type": "string"},
    },
    "required": ["chosen", "rejected"],
}

prompt = FormatStrPrompt(
    f_str_template=(
        "为下面用户问题生成一组 DPO 偏好回答。"
        "chosen 必须完整、准确、有帮助；rejected 必须看似相关但明显更差，"
        "例如过短、遗漏关键约束、推理错误或没有回答问题。"
        "不要让 rejected 包含有害内容、隐私泄露、歧视或违法指导。\n\n"
        "用户问题：{question}"
    )
)

FormatStrPromptedGenerator(
    llm_serving=llm,
    system_prompt="You generate preference-pair training data.",
    prompt_template=prompt,
    json_schema=schema,
).run(storage=storage, output_key="dpo_pair", question="user_prompt")
storage = storage.step()
```

随后在 Pipeline 内解析 `dpo_pair`，写入 `gt` 和 `rejected_answer`，并复用已有偏好对清洗
链路。若模型返回无法解析、字段为空或两答案相同，该样本应丢弃并计入
`scenario_metrics.json`。

推荐从 [`dpo_pipeline.py`](dpo_pipeline.py) 复制模板：它使用
`LazyFileStorage`、`PandasOperator`、`FormatStrPromptedGenerator`、`GeneralFilter`、
`ContentNullFilter` 和 `HashDeduplicateFilter` 完成 input-only 到 DPO JSONL 的生成、
过滤和去重。

## 可选打标与过滤

- 需要 LLM 判断偏好强弱时，用 `FormatStrPromptedGenerator` 对
  `user_prompt/gt/rejected_answer` 输出 `preference_label`、`reason` 和可选分数；
  只保留明确选择 `gt` 的样本。
- `AlpagasusFilter` 可用于筛 chosen 质量，但它只看 instruction/response，不验证
  rejected 是否足够差。
- `RMFilter` / `RMSampleEvaluator` 默认下载本地 reward model 且默认 CUDA，不进入
  首批默认链路；除非运行环境和用户成本预期明确允许。

## 输出验证

最终 JSONL 只保留：

```json
{
  "id": "alpaca-gpt4-2",
  "system_prompt": "You are a helpful assistant.",
  "user_prompt": "用户问题",
  "gt": "推荐回答",
  "rejected_answer": "不推荐回答"
}
```

用 `df_run_pipeline(output_schema="dpo", model_profile="text")` 本地验证 Sample；用户确认
后再 `df_submit_pipeline(output_schema="dpo", model_profile="text")`。
