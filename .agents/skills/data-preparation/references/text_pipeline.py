"""Minimal DataFlow text pipeline template for canonical `text` output.

Usage:
    python text_pipeline.py <input.jsonl> <processed.jsonl>
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import dataflow


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
_module = importlib.util.module_from_spec(_spec)
sys.modules["dataflow.serving.api_llm_serving_request"] = _module
_spec.loader.exec_module(_module)
APILLMServing_request = _module.APILLMServing_request

from dataflow.operators.core_text import PromptedGenerator  # noqa: E402
from dataflow.utils.storage import LazyFileStorage  # noqa: E402
from df_logging import LoggingLLMServing  # noqa: E402


SYSTEM_PROMPT = "You are a helpful assistant."


def source_id(row: dict, index: int) -> str:
    return str(row.get("id") or f"text-{index}")


def build_user_prompt(row: dict) -> str:
    value = row.get("user_prompt") or row.get("prompt")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source record must provide a non-empty prompt")
    return value.strip()


def main(input_path: str, output_path: str) -> None:
    storage = LazyFileStorage(str(Path(input_path).resolve()), cache_type="jsonl")
    storage = storage.step()

    raw_llm = APILLMServing_request(
        api_url=os.environ["DF_API_URL"],
        model_name=os.environ["DF_MODEL_NAME"],
        key_name_of_api_key="DF_API_KEY",
        temperature=0.0,
        max_workers=8,
    )
    llm = LoggingLLMServing(raw_llm)

    generator = PromptedGenerator(
        llm_serving=llm,
        system_prompt=SYSTEM_PROMPT,
    )
    generator.run(storage=storage, input_key="prompt", output_key="generated_answer")
    storage = storage.step()

    rows = storage.read(output_type="dict")
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as sink:
        for index, row in enumerate(rows):
            user_prompt = build_user_prompt(row)
            answer = row.get("generated_answer")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("generated_answer must be a non-empty string")
            sink.write(
                json.dumps(
                    {
                        "id": source_id(row, index),
                        "system_prompt": SYSTEM_PROMPT,
                        "user_prompt": user_prompt,
                        "gt": answer.strip(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: text_pipeline.py <input.jsonl> <processed.jsonl>")
    main(sys.argv[1], sys.argv[2])
