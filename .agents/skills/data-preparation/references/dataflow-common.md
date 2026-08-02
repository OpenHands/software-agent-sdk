# DataFlow 通用约定

## Pipeline 骨架

所有场景使用 `LazyFileStorage`，每个算子执行后都推进一次 step：

```python
storage = LazyFileStorage(input_path, cache_type="jsonl")
storage = storage.step()

operator.run(storage=storage, ...)
storage = storage.step()

rows = storage.read(output_type="dict")
```

LLM 配置只读取 `DF_API_URL`、`DF_MODEL_NAME` 和 `DF_API_KEY`。使用
`APILLMServing_request`，并立即包装：

```python
raw_llm = APILLMServing_request(
    api_url=os.environ["DF_API_URL"],
    model_name=os.environ["DF_MODEL_NAME"],
    key_name_of_api_key="DF_API_KEY",
    max_workers=8,
)
llm = LoggingLLMServing(raw_llm)
```

直接导入 `dataflow.serving` 可能加载无关重依赖；复制
[`text_pipeline.py`](text_pipeline.py) 中的 importlib shim。

## 输出与审计

- 算子中间列不是正式产物。Pipeline 最后只写
  [`schema-conventions.md`](schema-conventions.md) 允许的字段。
- 模型、源路径、调用耗时、重试、难度和过滤原因不得混入训练行。
- 场景汇总可写入 `DF_LOG_DIR/scenario_metrics.json`，内容必须是 JSON object；
  `generate_report.py` 会把它放入 `report.json.scenario_metrics`。
- DataFlow Checkpoint、`llm_calls.jsonl`、`failure.json` 和 `validation.json` 由共享
  runtime 管理，Pipeline 不复制这些脚本。

## 资源与版本

- 本地和 Pyromind 都使用 `open-dataflow==1.0.10`。
- Text2SQL 算子在 Python 3.13 存在上游 `re.template` 兼容问题，本地 Sample 使用
  Python 3.10 的 `DATAFLOW_PYTHON`。
- 只使用 API Chat/Vision 和 CPU 算子；需要 Embedding、CUDA、外部服务或模型下载的
  算子不进入首批链路。
