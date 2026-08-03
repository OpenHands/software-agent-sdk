"""DataFlow DPO pipeline template.

Usage:
    pipeline.py <input.jsonl> <processed.jsonl> [limit]

This template is for input-only or partially labeled records. It uses DataFlow
operators for normalization, generation, filtering, and deduplication, then writes
the canonical Pyromind DPO JSONL schema.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from typing import Any

import dataflow
import pandas as pd
from dataflow.operators.core_text import (
    FormatStrPromptedGenerator,
    GeneralFilter,
    PandasOperator,
)
from dataflow.operators.general_text import ContentNullFilter, HashDeduplicateFilter
from dataflow.prompts.core_text import FormatStrPrompt
from dataflow.utils.storage import LazyFileStorage
from df_logging import LoggingLLMServing


_pkg = types.ModuleType("dataflow.serving")
_pkg.__path__ = []
sys.modules["dataflow.serving"] = _pkg
_serving_file = (
    Path(dataflow.__file__).parent / "serving" / "api_llm_serving_request.py"
)
_spec = importlib.util.spec_from_file_location(
    "dataflow.serving.api_llm_serving_request",
    str(_serving_file),
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["dataflow.serving.api_llm_serving_request"] = _mod
_spec.loader.exec_module(_mod)
APILLMServing_request = _mod.APILLMServing_request


DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


def _first_string(row: pd.Series, names: list[str], default: str = "") -> str:
    for name in names:
        value = row.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["id"] = [
        _first_string(row, ["id"], f"dpo-{index}")
        for index, row in result.iterrows()
    ]
    result["system_prompt"] = [
        _first_string(row, ["system_prompt", "system"], DEFAULT_SYSTEM_PROMPT)
        for _, row in result.iterrows()
    ]
    result["user_prompt"] = [
        _first_string(row, ["user_prompt", "prompt", "question", "input"])
        for _, row in result.iterrows()
    ]
    result["existing_gt"] = [
        _first_string(row, ["gt", "chosen", "preferred", "response"])
        for _, row in result.iterrows()
    ]
    result["existing_rejected"] = [
        _first_string(row, ["rejected_answer", "rejected", "bad"])
        for _, row in result.iterrows()
    ]
    return result


def parse_pair(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        payload = value
    elif isinstance(value, str):
        payload = json.loads(value)
    else:
        raise ValueError("dpo_pair must be a JSON object or JSON object string")
    chosen = payload.get("chosen")
    rejected = payload.get("rejected")
    if not isinstance(chosen, str) or not isinstance(rejected, str):
        raise ValueError("dpo_pair must contain string chosen and rejected")
    return {"chosen": chosen.strip(), "rejected": rejected.strip()}


def apply_generated_pair(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    chosen_values: list[str] = []
    rejected_values: list[str] = []
    parse_ok: list[bool] = []
    for _, row in result.iterrows():
        existing_gt = row.get("existing_gt")
        existing_rejected = row.get("existing_rejected")
        try:
            parsed = parse_pair(row.get("dpo_pair"))
            chosen = existing_gt if isinstance(existing_gt, str) and existing_gt else parsed["chosen"]
            rejected = (
                existing_rejected
                if isinstance(existing_rejected, str) and existing_rejected
                else parsed["rejected"]
            )
            parse_ok.append(bool(chosen.strip() and rejected.strip()))
            chosen_values.append(chosen.strip())
            rejected_values.append(rejected.strip())
        except Exception:
            parse_ok.append(False)
            chosen_values.append("")
            rejected_values.append("")
    result["gt"] = chosen_values
    result["rejected_answer"] = rejected_values
    result["dpo_pair_parse_ok"] = parse_ok
    return result


def write_canonical_output(input_path: str, output_path: str, limit: int | None) -> None:
    storage = LazyFileStorage(
        input_path,
        cache_path=str(Path(output_path).parent / ".dataflow_cache"),
        file_name_prefix="dpo",
        cache_type="jsonl",
    )
    storage = storage.step()

    if limit is not None:
        PandasOperator([lambda df: df.head(limit).copy()]).run(storage=storage)
        storage = storage.step()

    PandasOperator([normalize_columns]).run(storage=storage)
    storage = storage.step()

    ContentNullFilter().run(storage=storage, input_key="user_prompt")
    storage = storage.step()

    raw_llm = APILLMServing_request(
        api_url=os.environ["DF_API_URL"],
        model_name=os.environ["DF_MODEL_NAME"],
        key_name_of_api_key="DF_API_KEY",
        max_workers=int(os.environ.get("DF_MAX_WORKERS", "8")),
    )
    llm = LoggingLLMServing(raw_llm)

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
            "为下面用户问题生成 DPO 偏好回答对。"
            "如果给出了现有优质回答，请把它作为 chosen 的依据；否则生成一个完整、准确、有帮助的 chosen。"
            "rejected 必须看似相关但明显更差，例如过短、遗漏关键约束、推理错误或没有回答问题。"
            "不要让 rejected 包含有害内容、隐私泄露、歧视或违法指导。"
            "只返回 JSON：{\"chosen\":\"...\",\"rejected\":\"...\"}\n\n"
            "用户问题：{question}\n"
            "现有优质回答（可为空）：{chosen_seed}\n"
            "现有负样本（可为空）：{rejected_seed}"
        )
    )
    FormatStrPromptedGenerator(
        llm_serving=llm,
        system_prompt="You generate preference-pair training data.",
        prompt_template=prompt,
        json_schema=schema,
    ).run(
        storage=storage,
        output_key="dpo_pair",
        question="user_prompt",
        chosen_seed="existing_gt",
        rejected_seed="existing_rejected",
    )
    storage = storage.step()

    PandasOperator([apply_generated_pair]).run(storage=storage)
    storage = storage.step()

    GeneralFilter(
        [
            lambda df: df["dpo_pair_parse_ok"],
            lambda df: df["gt"].str.strip() != df["rejected_answer"].str.strip(),
        ]
    ).run(storage=storage)
    storage = storage.step()

    for key in ["system_prompt", "user_prompt", "gt", "rejected_answer"]:
        ContentNullFilter().run(storage=storage, input_key=key)
        storage = storage.step()

    HashDeduplicateFilter(hash_func="md5").run(
        storage=storage,
        input_keys=["system_prompt", "user_prompt", "gt", "rejected_answer"],
    )
    storage = storage.step()

    rows = storage.read(output_type="dict")
    with open(output_path, "w", encoding="utf-8") as target:
        for row in rows:
            target.write(
                json.dumps(
                    {
                        "id": str(row["id"]),
                        "system_prompt": str(row["system_prompt"]).strip(),
                        "user_prompt": str(row["user_prompt"]).strip(),
                        "gt": str(row["gt"]).strip(),
                        "rejected_answer": str(row["rejected_answer"]).strip(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


if __name__ == "__main__":
    if len(sys.argv) not in {3, 4}:
        raise SystemExit("usage: pipeline.py <input.jsonl> <processed.jsonl> [limit]")
    write_canonical_output(
        sys.argv[1],
        sys.argv[2],
        int(sys.argv[3]) if len(sys.argv) == 4 else None,
    )
