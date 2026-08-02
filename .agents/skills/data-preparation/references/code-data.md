# Code 数据

## 适用

从主题生成代码指令、从代码反推指令、根据指令生成代码，并进行静态/LLM 质量评分。
最终输出 `text`。

## 推荐链路

```text
CodeInstructionGenerator
  或 CodeCodeToInstructionGenerator
→ CodeInstructionToCodeGenerator
→ CodeQualitySampleEvaluator
→ CodeQualityScoreFilter
```

```python
CodeInstructionToCodeGenerator(llm_serving=llm).run(
    storage=storage,
    input_instruction_key="instruction",
    output_code_key="code",
)
storage = storage.step()
```

最终映射 `instruction → user_prompt`、`code → gt`；语言、质量分和来源进入
`scenario_metrics.json`。

只做文本生成和静态/模型评分。禁止使用 `CodeSandboxSampleEvaluator`，不得执行、
编译或安装生成代码。
