#!/usr/bin/env python3
"""
Example DataFlow pipeline: generate chain-of-thought for DAPO-Math-17k

Key API notes:
- Class is APILLMServing_request (underscore)
- Must use importlib shim to import it (dataflow/serving/__init__.py pulls
  in transformers/torch which fails in sandboxed or version-conflict envs)
- PromptedGenerator constructor: llm_serving, system_prompt, user_prompt, json_schema
- input_key / output_key are parameters of run(), NOT the constructor
- generator.run() returns the output_key string, NOT a storage object
- After run(), data is buffered at the next step; call storage.step() then read()
"""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import dataflow  # top-level package is safe, does not import torch/transformers


# --- Import shim: bypass dataflow/serving/__init__.py ---
_pkg = types.ModuleType("dataflow.serving")
_pkg.__path__ = []
sys.modules["dataflow.serving"] = _pkg
_serving_file = (
    Path(dataflow.__file__).parent / "serving" / "api_llm_serving_request.py"
)
_spec = importlib.util.spec_from_file_location(
    "dataflow.serving.api_llm_serving_request", str(_serving_file)
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["dataflow.serving.api_llm_serving_request"] = _mod
_spec.loader.exec_module(_mod)
APILLMServing_request = _mod.APILLMServing_request
# --- End shim ---

from dataflow.operators.core_text import PromptedGenerator  # noqa: E402
from dataflow.utils.storage import LazyFileStorage  # noqa: E402


SYSTEM_PROMPT = (
    "你是一个严谨的数学推理助手。对于每一道数学题，请先一步步思考推理过程，"
    "然后给出最终答案。你的回答应包含两部分：\n"
    "- 思考过程（reasoning），用中文详细说明\n"
    "- 最终答案（answer），用简洁的数值或表达式给出\n"
    "按以下JSON格式输出：\n"
    '{"reasoning": "...", "answer": "..."}'
)


def main(input_path: str, output_path: str) -> None:
    input_file = Path(input_path).resolve()
    output_file = Path(output_path).resolve()

    # 1. Initialize storage and advance to step 0 (reads the input file)
    storage = LazyFileStorage(str(input_file), cache_type="jsonl")
    storage = storage.step()

    # 2. Initialize LLM from environment variables (injected by df_run_pipeline)
    llm = APILLMServing_request(
        api_url=os.environ.get(
            "DF_API_URL", "https://api.openai.com/v1/chat/completions"
        ),
        model_name=os.environ.get("DF_MODEL_NAME", "gpt-4o-mini"),
        key_name_of_api_key="DF_API_KEY",
        temperature=0.0,
        max_workers=8,
    )

    # 3. Generator: add CoT reasoning
    #    Constructor only accepts: llm_serving, system_prompt, user_prompt, json_schema
    generator = PromptedGenerator(
        llm_serving=llm,
        system_prompt=SYSTEM_PROMPT,
    )
    # input_key / output_key are parameters of run(), not the constructor
    generator.run(storage=storage, input_key="problem", output_key="cot")

    # 4. Read results: run() buffered data at step 1, advance and read
    storage = storage.step()
    data = storage.read(output_type="dict")

    # 5. Write final output to the requested path
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Processed {len(data)} records, written to {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input.jsonl> [output.jsonl]", file=sys.stderr)
        sys.exit(1)
    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "processed.jsonl"
    main(input_path, output_path)
