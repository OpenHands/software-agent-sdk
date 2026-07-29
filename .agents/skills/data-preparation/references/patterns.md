# DataFlow Pipeline 常见模式

以下代码片段都已适配本环境（importlib shim + LazyFileStorage + 环境变量凭证）。
仅展示 `main()` 函数体内的算子编排逻辑，导入和 shim 部分参见
[算子与脚本约定](operators.md) 及 [example_pipeline.py](example_pipeline.py)。
注意：GeneralFilter 的 lambda 中使用了 `pd.to_numeric`，脚本顶部需 `import pandas as pd`。

---

## 模式 1: 生成 + 质量过滤

场景：从单字段生成内容，然后用 LLM 打分过滤低质量行。

```python
from dataflow.operators.core_text import PromptedGenerator, PromptedFilter

def main(input_path, output_path):
    storage = LazyFileStorage(input_path, cache_type="jsonl")
    storage = storage.step()

    llm = APILLMServing_request(
        api_url=os.environ["DF_API_URL"],
        model_name=os.environ["DF_MODEL_NAME"],
        key_name_of_api_key="DF_API_KEY",
        max_workers=8,
    )

    # 步骤 1: 生成
    generator = PromptedGenerator(
        llm_serving=llm,
        system_prompt="为这道数学题写详细的解题过程。",
    )
    generator.run(storage=storage, input_key="problem", output_key="solution")
    storage = storage.step()

    # 步骤 2: LLM 打分过滤（保留 4-5 分）
    quality_filter = PromptedFilter(
        llm_serving=llm,
        system_prompt="评估解题过程的质量，从 1-5 打分。考虑正确性、完整性和清晰度。",
        min_score=4,
        max_score=5,
    )
    quality_filter.run(storage=storage, input_key="solution", output_key="quality_score")
    storage = storage.step()

    # 读取最终结果
    data = storage.read(output_type="dict")
    with open(output_path, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
```

---

## 模式 2: 多字段评分 + 规则过滤

场景：组合多个字段进行评分，然后用确定性规则过滤。
关键：多字段 → 必须用 `FormatStrPromptedGenerator`，不要用多个 PromptedGenerator。

```python
from dataflow.operators.core_text import FormatStrPromptedGenerator, GeneralFilter
from dataflow.prompts.core_text import FormatStrPrompt

def main(input_path, output_path):
    storage = LazyFileStorage(input_path, cache_type="jsonl")
    storage = storage.step()

    llm = APILLMServing_request(
        api_url=os.environ["DF_API_URL"],
        model_name=os.environ["DF_MODEL_NAME"],
        key_name_of_api_key="DF_API_KEY",
        max_workers=8,
    )

    # 步骤 1: 多字段组合评分
    prompt_template = FormatStrPrompt(
        f_str_template=(
            "请评估这条训练样本的质量。\n"
            "指令: {instruction}\n"
            "回答: {response}\n"
            "只返回一个 1-5 的整数分数。"
        )
    )
    scorer = FormatStrPromptedGenerator(
        llm_serving=llm,
        system_prompt="你是严格的数据质量评估员。",
        prompt_template=prompt_template,
    )
    # kwargs: key=模板变量名, value=DataFrame列名
    scorer.run(
        storage=storage,
        output_key="quality_score",
        instruction="instruction",   # 模板 {instruction} → 列 "instruction"
        response="output",           # 模板 {response} → 列 "output"
    )
    storage = storage.step()

    # 步骤 2: 规则过滤（分数 >= 4，健壮解析 LLM 输出）
    rule_filter = GeneralFilter([
        lambda df: pd.to_numeric(
            df["quality_score"].str.extract(r"(\d+)")[0], errors="coerce"
        ).fillna(0) >= 4,
    ])
    rule_filter.run(storage=storage)
    storage = storage.step()

    data = storage.read(output_type="dict")
    with open(output_path, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
```

---

## 模式 3: 多阶段流水线（评分 → 初筛 → 空行过滤 → 改写 → 再评分 → 过滤）

场景：先初筛，再对中等质量数据改写提升，最终过滤。
关键：多个 PromptedGenerator 阶段允许，但每阶段必须有不同语义职责。PromptedRefiner 前必须确保 input_key 无空值。

```python
from dataflow.operators.core_text import PromptedGenerator, PromptedRefiner, GeneralFilter

def main(input_path, output_path):
    storage = LazyFileStorage(input_path, cache_type="jsonl")
    storage = storage.step()

    llm = APILLMServing_request(
        api_url=os.environ["DF_API_URL"],
        model_name=os.environ["DF_MODEL_NAME"],
        key_name_of_api_key="DF_API_KEY",
        max_workers=8,
    )

    # 步骤 1: 初始评分
    init_scorer = PromptedGenerator(
        llm_serving=llm,
        system_prompt="对这段文本质量打分 1-5，只返回整数。",
    )
    init_scorer.run(storage=storage, input_key="raw_content", output_key="init_score")
    storage = storage.step()

    # 步骤 2: 过滤掉极低分（< 2 分直接丢弃，健壮解析）
    pre_filter = GeneralFilter([
        lambda df: pd.to_numeric(
            df["init_score"].str.extract(r"(\d+)")[0], errors="coerce"
        ).fillna(0) >= 2,
    ])
    pre_filter.run(storage=storage)
    storage = storage.step()

    # 步骤 3: 过滤空行（避免 PromptedRefiner 长度不匹配报错）
    empty_filter = GeneralFilter([
        lambda df: df["raw_content"].str.len() > 0,
    ])
    empty_filter.run(storage=storage)
    storage = storage.step()

    # 步骤 4: 改写润色（覆盖原字段）
    refiner = PromptedRefiner(
        llm_serving=llm,
        system_prompt="改写这段文本，提升清晰度和完整性，保持原意。",
    )
    refiner.run(storage=storage, input_key="raw_content")  # 覆盖 raw_content
    storage = storage.step()

    # 步骤 5: 最终评分
    final_scorer = PromptedGenerator(
        llm_serving=llm,
        system_prompt="对改写后的文本质量打分 1-5，只返回整数。",
    )
    final_scorer.run(storage=storage, input_key="raw_content", output_key="final_score")
    storage = storage.step()

    # 步骤 6: 最终过滤
    final_filter = GeneralFilter([
        lambda df: pd.to_numeric(
            df["final_score"].str.extract(r"(\d+)")[0], errors="coerce"
        ).fillna(0) >= 4,
    ])
    final_filter.run(storage=storage)
    storage = storage.step()

    data = storage.read(output_type="dict")
    with open(output_path, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
```

---

## 模式 4: 规则清洗流水线（不需要 LLM）

场景：纯规则级内容清洗——去 emoji、语言过滤、词数过滤、去重。
关键：不需要 APILLMServing_request，不需要 importlib shim（除非混用 core_text 算子）。

```python
from dataflow.operators.general_text import (
    RemoveEmojiRefiner, RemoveExtraSpacesRefiner,
    LanguageFilter, WordNumberFilter, HashDeduplicateFilter,
)

def main(input_path, output_path):
    storage = LazyFileStorage(input_path, cache_type="jsonl")
    storage = storage.step()

    # 步骤 1: 去 emoji + 压缩空格
    RemoveEmojiRefiner().run(storage=storage, input_key="text")
    storage = storage.step()

    RemoveExtraSpacesRefiner().run(storage=storage, input_key="text")
    storage = storage.step()

    # 步骤 2: 语言过滤（只保留中文/英文）
    LanguageFilter(allowed_languages=["zh", "en"]).run(
        storage=storage, input_key="text"
    )
    storage = storage.step()

    # 步骤 3: 词数过滤
    WordNumberFilter(min_words=10, max_words=50000).run(
        storage=storage, input_key="text"
    )
    storage = storage.step()

    # 步骤 4: 精确去重
    HashDeduplicateFilter(hash_func="md5").run(
        storage=storage, input_key="text"
    )
    storage = storage.step()

    data = storage.read(output_type="dict")
    with open(output_path, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
```

---

## 模式选择指南

| 用户需求 | 推荐模式 | 核心算子 |
|---------|---------|--------|
| "给数据加一列生成内容" | 模式 1（去掉 filter 部分） | PromptedGenerator |
| "生成内容并保证质量" | 模式 1 | PromptedGenerator + PromptedFilter |
| "根据多个字段打分/评估" | 模式 2 | FormatStrPromptedGenerator + GeneralFilter |
| "清洗/改写/提升质量" | 模式 3 | PromptedGenerator + PromptedRefiner + GeneralFilter |
| "从文档生成 QA 对" | 用 Text2MultiHopQAGenerator 替代模式 1 的 generator | Text2MultiHopQAGenerator |
| "去 emoji/过滤语言/去重/词数过滤" | 模式 4 | general_text Filter + Refiner |
| "先规则清洗再 LLM 生成" | 模式 4 + 模式 1 串联 | general_text + core_text |
