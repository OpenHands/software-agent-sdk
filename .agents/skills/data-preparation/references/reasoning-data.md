# Reasoning 数据

## 适用

合成可解的推理问题、生成答案、校验格式或 ground truth，并评估类别和难度。输入可为
领域文本、种子问题或已有问题；输出 `text`。

## 推荐链路

```text
ReasoningQuestionGenerator
→ ReasoningQuestionFilter
→ ReasoningAnswerGenerator
→ ReasoningAnswerFormatterFilter
→ ReasoningAnswerGroundTruthFilter（存在参考答案时）
→ ReasoningAnswerNgramFilter
→ ReasoningQuestionCategorySampleEvaluator
→ ReasoningQuestionDifficultySampleEvaluator
```

每个阶段执行后 `storage = storage.step()`。没有参考答案时跳过 GroundTruthFilter；
NgramFilter 只用于抑制模板化或重复答案。

最终映射问题到 `user_prompt`、完整答案到 `gt`。默认不额外提取或保存隐藏
`reasoning_content`；分类、难度和过滤原因写入 `scenario_metrics.json`。

不使用需要本地模型、额外 tokenizer 或 GPU 的 token evaluator。
