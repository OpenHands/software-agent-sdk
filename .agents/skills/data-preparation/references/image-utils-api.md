# image_utils API

`image_utils.py` 由 `df_run_pipeline` 和 `df_submit_pipeline` 自动投递。Pipeline
只能显式导入以下 API：

```python
from image_utils import ImagePipelineConfig, run_image_pipeline_from_cli
```

## ImagePipelineConfig

必填：

- `labeling_system_prompt`：发送给 VLM 的任务规则。
- `training_system_prompt`：写入最终训练数据的 `system_prompt`。

常用字段映射：

- `id_key="id"`
- `images_key="images"`
- `image_labels_key="image_labels"`
- `user_prompt_key="user_prompt"`
- `sample_system_prompt_key=None`
- `user_prompt_template=None`

Prompt 优先读取样本的 `user_prompt_key`；为空时使用
`user_prompt_template.format_map(sample)`。模板引用缺失字段会立即失败。

响应配置：

- `response_json_schema`：传给 DataFlow VLM Serving 的严格 JSON Schema。
- `reasoning_key="reasoning"`
- `answer_key="answer"`
- `answer_is_json=False`

执行配置：

- `batch_size=8`：失败时整个未提交 batch 重跑；设为 1 可获得逐条恢复。
- `max_attempts=3`
- `max_workers=8`
- `timeout=1800`

模型配置统一来自 `DF_API_KEY`、`DF_API_BASE_URL` 和 `DF_MODEL_NAME`，不得写入
Pipeline。

## 执行入口

标准脚本结尾：

```python
if __name__ == "__main__":
    run_image_pipeline_from_cli(CONFIG)
```

该入口解析固定的 `input_path output_path [limit]` 参数，构建并运行 DataFlow
Pipeline。需要程序化调用时可使用：

```python
run_image_pipeline(CONFIG, input_path, output_path, limit)
```

`MultiImageSemanticLabelOperator` 是底层 DataFlow Operator，主要用于测试或受控扩展；
常规 Agent Pipeline 不直接实例化。
