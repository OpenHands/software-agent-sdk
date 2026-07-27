---
name: data-preparation
description: >-
  用 DataFlow 算子对数据做内容级处理：规则清洗（词数/语言/去重/PII/毒性/去 emoji）、
  LLM 智能处理（生成 QA/CoT、语义评分过滤、文本改写、多字段推理）。
  支持从 HuggingFace 下载或读取本地/Storage JSONL，转换为 Pyromind 格式并上传。
  适用于“数据质量不好”“要过滤/清洗内容”“生成新数据”等内容级任务。
---

# 数据准备

所有数据读取、DataFlow 清洗和上传动作都在本地工作区执行，通过标准工具进行，不与 DataFlow 平台服务交互。

## 工作流

1. 如果用户提供 HuggingFace 数据集 ID，先用 `dataset_download` 下载前 5 条预览，让用户确认字段结构、split 和 config。下载路径放在 `public_data/data-preparation/`，注意：**output_path 必须是相对于 workspace 根目录的路径**，比如 `public_data/data-preparation/sample.jsonl`，不要加 `conversations/<id>/` 前缀。
2. 确认要生成/清洗的目标后，根据「算子选择决策表」选择合适算子，编写 pipeline.py。
3. 先用 `df_run_pipeline` 执行，传 `limit=3` 或仅 3 条样本，检查输出是否符合预期。
4. 确认后用完整输入执行 `df_run_pipeline`。
5. 用 `df_convert` 把 `processed.jsonl` 转换为 `messages.jsonl` 或 `preference.jsonl`。
6. 用 `upload_file_to_pyromind` 把最终产物上传到 Pyromind Storage。

---

## 算子选择决策表（MANDATORY）

按顺序匹配第一个命中的行：

| 任务/场景 | 必须使用 | 不要用 |
|-----------|---------|--------|
| 从文本生成 QA 对 | `Text2MultiHopQAGenerator` | PromptedGenerator + QA prompt |
| 多字段组合生成/评分 | `FormatStrPromptedGenerator` + `GeneralFilter` | 多个 PromptedGenerator |
| 确定性规则过滤（已有字段的数值比较） | `GeneralFilter` | PromptedFilter |
| LLM 语义质量过滤（单字段） | `PromptedFilter` | GeneralFilter |
| 单字段生成新内容 | `PromptedGenerator` | — |
| 文本改写/润色（覆盖原字段） | `PromptedRefiner` | PromptedGenerator |
| 词数/长度/语言/去重/PII/毒性/黑名单 | `general_text` 算子 | 自写 lambda 或 LLM |
| 去 emoji/HTML/多余空格/拼写纠正 | `general_text` Refiner | PromptedRefiner |

**原则**：
- 能用规则解决的内容清洗，优先用 `general_text` 算子（快、免费、确定性）。
- `PromptedGenerator` 是单字段 LLM 生成的兖底。
- 如果任务提到“QA”“问答”，优先 `Text2MultiHopQAGenerator`；如果 prompt 需要组合 2+ 个字段，必须用 `FormatStrPromptedGenerator`。

---

## 字段依赖规则（MANDATORY）

1. **先检查样本**：识别用户数据中所有可用字段
2. **字段存在性**：步骤 N 需要字段 X，则 X 必须在原始数据中存在，或由步骤 M（M < N）产出
3. **不能先引用后创建**：不能在生成 `quality_score` 之前 filter 它
4. **避免覆盖**：不要覆盖用户原始字段，除非明确要求

```
✗ 错误: GeneralFilter([lambda df: df["score"] >= 4])  ← score 还不存在
✓ 正确: 先 PromptedGenerator → output_key="score"，再 GeneralFilter
```

---

## 核心算子 API 签名

### 基础组件

**`LazyFileStorage`**（我们的环境用这个，不是 FileStorage）

```python
from dataflow.utils.storage import LazyFileStorage
storage = LazyFileStorage("input.jsonl", cache_type="jsonl")
storage = storage.step()  # 推进到 step 0，读入数据
```

**`APILLMServing_request`**（必须用 importlib shim 导入，见下方脚本约定）

```python
llm = APILLMServing_request(
    api_url=os.environ["DF_API_URL"],       # 由 df_run_pipeline 自动注入
    model_name=os.environ["DF_MODEL_NAME"], # 由 df_run_pipeline 自动注入
    key_name_of_api_key="DF_API_KEY",       # 环境变量名，不是 key 本身
    max_workers=8,
)
```

### 1) PromptedGenerator — 单字段 LLM 生成

```python
from dataflow.operators.core_text import PromptedGenerator

generator = PromptedGenerator(
    llm_serving=llm,
    system_prompt="You are a helpful agent.",
    user_prompt="",           # 拼接在 input 前面的前缀
    json_schema=None,         # 可选，结构化输出
)
# input_key / output_key 是 run() 的参数！
generator.run(storage=storage, input_key="problem", output_key="cot")
```

- `run()` 返回 `output_key` 字符串，**不是** storage
- 数据缓冲在下一个 step，需要 `storage = storage.step()` 后才能 `read()`

### 2) FormatStrPromptedGenerator — 多字段模板生成

```python
from dataflow.operators.core_text import FormatStrPromptedGenerator
from dataflow.prompts.core_text import FormatStrPrompt

prompt_template = FormatStrPrompt(
    f_str_template="请评估这条数据。问题: {question}; 答案: {answer}。返回 1-5 分。"
)
scorer = FormatStrPromptedGenerator(
    llm_serving=llm,
    system_prompt="You are a strict evaluator.",
    prompt_template=prompt_template,  # 不能为 None！
)
# kwargs: key=模板变量名, value=DataFrame列名
scorer.run(storage=storage, output_key="score", question="problem", answer="solution")
```

- `**input_keys` 的 key 必须匹配模板中的 `{placeholder}` 名
- `**input_keys` 的 value 必须是已存在的 DataFrame 列名
- `prompt_template` 不能为 None（会 raise ValueError）

### 3) PromptedFilter — LLM 语义过滤

```python
from dataflow.operators.core_text import PromptedFilter

filter_op = PromptedFilter(
    llm_serving=llm,
    system_prompt="Evaluate quality on scale 1-5.",
    min_score=4,
    max_score=5,
)
filter_op.run(storage=storage, input_key="generated_content", output_key="eval")
```

- 只接受单个 `input_key`（多字段评分用 FormatStrPromptedGenerator + GeneralFilter）
- `input_key` 为空的行会被**静默丢弃**
- 保留分数在 `[min_score, max_score]` 闭区间内的行

### 4) GeneralFilter — 规则过滤

```python
from dataflow.operators.core_text import GeneralFilter

filter_op = GeneralFilter([
    lambda df: df["score"].astype(int) >= 4,
    lambda df: df["length"] > 100,
])
filter_op.run(storage=storage)  # 无 input_key/output_key
```

- 每条规则返回布尔 Series，多条规则 AND 组合
- 引用的字段必须已存在
- **LLM 评分解析**：LLM 可能返回 `"4/5"` 等非纯数字，用 `str.extract` 更健壮：
  ```python
  lambda df: pd.to_numeric(
      df["score"].str.extract(r"(\d+)")[0], errors="coerce"
  ).fillna(0) >= 4
  ```
  同时在 system_prompt 中强调"只返回一个整数"以降低解析失败率

### 5) PromptedRefiner — LLM 改写/润色

```python
from dataflow.operators.core_text import PromptedRefiner

refiner = PromptedRefiner(
    llm_serving=llm,
    system_prompt="Rewrite for clarity and completeness.",
)
refiner.run(storage=storage, input_key="raw_content")  # 覆盖原字段！
```

- **覆盖** input_key 列；需保留原文时先拷贝到新列
- **空值陷阱**：input_key 为空的行不会发给 LLM，导致输出长度 < 行数而报错。使用前先用 `GeneralFilter([lambda df: df["raw_content"].str.len() > 0])` 过滤空行

### 6) Text2MultiHopQAGenerator — 多跳 QA 生成

```python
from dataflow.operators.core_text import Text2MultiHopQAGenerator

qa_gen = Text2MultiHopQAGenerator(
    llm_serving=llm,
    seed=0,
    lang="zh",       # 控制句子分割（"。" vs "."）
    num_q=5,         # 每行最多保留的 QA 对数
)
qa_gen.run(storage=storage, input_key="cleaned_text", output_key="QA_pairs")
```

- 输出是嵌套 list of dict（`question`, `answer`, `reasoning_steps`, `supporting_facts`）
- **不是**独立的 question/answer 列！下游不能直接引用
- 输入文本约束：100–200,000 字符，至少 2 个句子，特殊字符 ≤ 30%

---

## 算子类别导航

| 类别 | 适用场景 | 不适用 |
|------|---------|--------|
| `core_text` | 通用文本→QA/生成/过滤/润色/评分，prompt-driven 操作首选 | PT 语料过滤、代码任务 |
| `reasoning` | 数学/推理 CoT 生成、难度评估、答案校验 | 通用文本清洗 |
| `general_text` | 纯规则清洗：长度/去重/PII/语言/HTML，**不需要 LLM** | 语义级过滤 |
| `text_sft` | 已有 SFT 数据后的质量评估：Deita/Alpagasus/RM | 从原始文本生成 QA |
| `text_pt` | 预训练语料：perplexity/FineWeb-Edu/CCNet 去重 | SFT/QA 数据 |
| `code` | 代码质量评分/过滤/生成 | 自然语言任务 |

---

## general_text 规则算子（不需要 LLM）

导入方式：`from dataflow.operators.general_text import XxxFilter, XxxRefiner`

所有 filter 的 `run()` 会直接过滤行（减少行数），并添加 output_key 列保存指标值。
所有 refiner 的 `run()` 会就地转换 input_key 列内容（行数不变）。

### 常用 Filter

```python
from dataflow.operators.general_text import (
    WordNumberFilter,       # 词数过滤
    LanguageFilter,         # 语言过滤（fasttext）
    HashDeduplicateFilter,  # 精确哈希去重
    MinHashDeduplicateFilter,  # 模糊去重（相似度阈值）
    BlocklistFilter,        # 关键词黑名单
    LexicalDiversityFilter, # 词汇多样性
)

# 词数过滤：保留 20–100000 词的文本
WordNumberFilter(min_words=20, max_words=100000).run(
    storage=storage, input_key="text"
)

# 语言过滤：只保留中文/英文
LanguageFilter(allowed_languages=["zh", "en"]).run(
    storage=storage, input_key="text"
)

# 精确去重（基于 md5）
HashDeduplicateFilter(hash_func="md5").run(
    storage=storage, input_key="text"
)

# 模糊去重（MinHash，相似度 > 0.9 视为重复）
MinHashDeduplicateFilter(num_perm=128, threshold=0.9).run(
    storage=storage, input_key="text"
)
```

### 常用 Refiner

```python
from dataflow.operators.general_text import (
    RemoveEmojiRefiner,         # 去 emoji
    RemoveExtraSpacesRefiner,   # 压缩多余空格
    HtmlUrlRemoverRefiner,      # 去 HTML/URL
    PIIAnonymizeRefiner,        # PII 匿名化（需 transformers+presidio）
    SpellingCorrectionRefiner,  # 拼写纠正
)

# 去 emoji（覆盖原字段）
RemoveEmojiRefiner().run(storage=storage, input_key="text")
```

### 组合示例：规则清洗流水线

```python
from dataflow.operators.general_text import (
    WordNumberFilter, LanguageFilter, RemoveEmojiRefiner,
)

def main(input_path, output_path):
    storage = LazyFileStorage(input_path, cache_type="jsonl")
    storage = storage.step()

    # 步骤 1: 去 emoji
    RemoveEmojiRefiner().run(storage=storage, input_key="text")
    storage = storage.step()

    # 步骤 2: 语言过滤
    LanguageFilter(allowed_languages=["zh", "en"]).run(
        storage=storage, input_key="text"
    )
    storage = storage.step()

    # 步骤 3: 词数过滤
    WordNumberFilter(min_words=10, max_words=50000).run(
        storage=storage, input_key="text"
    )
    storage = storage.step()

    data = storage.read(output_type="dict")
    with open(output_path, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
```

> **注意**：general_text 算子不需要 LLM，因此不需要 APILLMServing_request 和 importlib shim。
> 但如果同一 pipeline 中混用 core_text（LLM）和 general_text（规则）算子，shim 仍然需要。

---

## 本地 DataFlow 脚本约定

必须保持以下接口（`df_run_pipeline` 的 CLI 契约）：

```python
# pipeline.py <input.jsonl> [output.jsonl]
import json, os, sys, types, importlib.util
from pathlib import Path

import pandas as pd
import dataflow  # 顶层包安全，不导入 torch/transformers

# --- 导入绕过 shim（MANDATORY）---
_pkg = types.ModuleType("dataflow.serving")
_pkg.__path__ = []
sys.modules["dataflow.serving"] = _pkg
_serving_file = Path(dataflow.__file__).parent / "serving" / "api_llm_serving_request.py"
_spec = importlib.util.spec_from_file_location(
    "dataflow.serving.api_llm_serving_request", str(_serving_file)
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["dataflow.serving.api_llm_serving_request"] = _mod
_spec.loader.exec_module(_mod)
APILLMServing_request = _mod.APILLMServing_request
# --- shim 结束 ---

from dataflow.utils.storage import LazyFileStorage
from dataflow.operators.core_text import PromptedGenerator

def main(input_path: str, output_path: str):
    storage = LazyFileStorage(input_path, cache_type="jsonl")
    storage = storage.step()

    llm = APILLMServing_request(
        api_url=os.environ["DF_API_URL"],
        model_name=os.environ["DF_MODEL_NAME"],
        key_name_of_api_key="DF_API_KEY",
    )
    generator = PromptedGenerator(llm_serving=llm, system_prompt="...")
    generator.run(storage=storage, input_key="problem", output_key="cot")

    storage = storage.step()
    data = storage.read(output_type="dict")
    with open(output_path, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "processed.jsonl"
    main(input_path, output_path)
```

**关键注意事项：**
- 类名是 `APILLMServing_request`（带下划线），不是 `APILLMServingRequest`
- **必须用 importlib shim 导入**，不要直接 `from dataflow.serving import ...`
- `PromptedGenerator` 构造函数只接受 `llm_serving`、`system_prompt`、`user_prompt`、`json_schema`
- `input_key` 和 `output_key` 是 `run()` 方法的参数
- `generator.run()` 返回 `output_key` 字符串，**不是** storage 对象
- 生成结果缓冲在 storage 的下一个 step，需要再调 `storage.step()` 后才能 `read()`
- 多步算子链：先 `storage = storage.step()` 初始化到 step 0，然后每个算子 `run()` 后必须 `storage = storage.step()` 推进，下一个算子才能读到上一步的产出
- `json_schema` 中每个 `"type": "object"` 必须包含 `"additionalProperties": false`，否则 API 500

通过 `df_run_pipeline` 调用时，环境变量会由工具自动注入，脚本不要自己处理凭证。

---

## 多步算子链的 storage 模式

```python
storage = LazyFileStorage(input_path, cache_type="jsonl")
storage = storage.step()  # step 0: 读入原始数据

# 步骤 1: 生成
generator.run(storage=storage, input_key="problem", output_key="cot")
storage = storage.step()  # step 1: 推进到生成结果

# 步骤 2: 过滤（在 step 1 的数据上操作）
filter_op.run(storage=storage)
storage = storage.step()  # step 2: 推进到过滤结果

# 步骤 3: 读取最终结果
data = storage.read(output_type="dict")
```

每个 `operator.run()` 在当前 step 的数据上操作并写入下一步；`storage.step()` 推进指针。

---

## 参考文件

- `references/example_pipeline.py` — 完整可运行的单算子示例
- `references/patterns.md` — 3 种常见模式的代码片段
- `references/target-formats.md` — df_convert 的目标格式说明
