# 多图语义打标

## 输入协议

每行使用同一份 manifest 协议：

```json
{
  "sample_id": "sample-001",
  "image_paths": ["images/a.jpg", "images/b.jpg"],
  "image_labels": ["视图A", "视图B"],
  "prompt": "任务描述",
  "reference_annotations": {},
  "metadata": {}
}
```

- 图片路径相对于 manifest；至少一张，顺序有语义。
- `image_labels` 可省略；提供时必须与图片一一对应。
- `reference_annotations` 保存可供模型参考或需要原样保留的标注。
- `metadata` 默认仅用于追踪，不注入模型。

## 执行方式

1. 根据用户目标、输入字段和规则，自主确定要生成的字段、允许模型读取的参考字段和
   原样保留字段；不要求额外 TaskSpec。
2. 用户可直接描述判断依据；必要时读取其指定或输入附近的相关说明文件，将有用规则浓缩进 prompt。没有额外依据时直接使用现有图片和字段。
3. 复制并按任务修改 [multimodal_pipeline.py](multimodal_pipeline.py) 顶部常量及小型
   hook，不创建 DataFlow operator。字段选择及理由必须写入 `FIELD_POLICY_RATIONALE`。
4. 默认首次通过 `df_run_pipeline` 传 `limit=3`，输出并检查
   `processed.sample.jsonl` 和 `processed.sample.report.json`。这是行为提示，
   不是程序门控；用户明确要求全量时直接运行全量。
5. 全量独立输出 `processed.jsonl` 和 `processed.report.json`。
6. 全量成功后单独调用 `df_convert(format="vision_sft_flat")` 生成
   `train.parquet`，再重新加载校验列、行数、图片路径顺序和可解码性。

模板直接调用
`APIVLMServing_openai.generate_from_input_multi_images`，支持任意图片数量、
样本级 prompt、JSON Schema、字段确定性合并和单样本失败隔离。

## Prompt 边界

- 将任务级角色、图片含义、判断依据和输出要求放入模板的 `SYSTEM_PROMPT`。
- 将样本级任务和允许使用的参考标注放入 `build_user_prompt`。
- `TRAINING_SYSTEM_PROMPT` 和 `TRAINING_PROMPT` 是最终训练提示；不得包含只在生成
  阶段使用的参考答案。
- `REASONING_FIELD` 和 `ANSWER_FIELD` 按当前任务指定。VLM 返回任务 JSON Schema，
  模板再确定性组装 `<think>/<answer>`，不要求 VLM 自己生成 XML。
- 结构化答案任务设置 `ANSWER_IS_JSON=True`，使 `<answer>` 内容额外经过 JSON 校验。
- 不要把整份 SOP 或无关文件塞入 prompt；只保留直接影响判断的规则。
- 报告中的 `field_policy` 用于审计本次自主决策，不是固定业务协议。

## AVI 示例

[avi_manifest_adapter.py](avi_manifest_adapter.py) 演示如何把
`defect.jpg`、`diff.jpg`、`gt.jpg` 和 `meta.json` 转换为通用 manifest。
它只是业务边界适配器，不是通用 pipeline 的依赖。
