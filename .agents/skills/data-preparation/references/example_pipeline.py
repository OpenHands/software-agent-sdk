#!/usr/bin/env python3
"""
Example DataFlow pipeline: generate chain-of-thought for DAPO-Math-17k

Batch-incremental mode: processes records in batches of BATCH_SIZE,
leveraging DataFlow's internal thread pool for concurrent LLM calls.
Each batch's results are written to disk immediately after completion.
Supports automatic resume on restart (skips records already in output).

Key API notes:
- Class is APILLMServing_request (underscore)
- Must use importlib shim to import it (dataflow/serving/__init__.py pulls
  in transformers/torch which fails in sandboxed or version-conflict envs)
- LoggingLLMServing wraps the LLM to record all calls (required for platform runs)
- generate_from_input() accepts a list and processes concurrently via max_workers
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

from df_logging import LoggingLLMServing  # noqa: E402


SYSTEM_PROMPT = (
    "你是一个严谨的数学推理助手。对于每一道数学题，请先一步步思考推理过程，"
    "然后给出最终答案。你的回答应包含两部分：\n"
    "- 思考过程（reasoning），用中文详细说明\n"
    "- 最终答案（answer），用简洁的数值或表达式给出\n"
    "按以下JSON格式输出：\n"
    '{"reasoning": "...", "answer": "..."}'
)

# Number of records per LLM batch. generate_from_input() processes these
# concurrently via its internal thread pool (max_workers=8).
BATCH_SIZE = 20


def _count_existing_lines(path: Path) -> int:
    """Count lines in existing output file for resume support."""
    if not path.is_file():
        return 0
    count = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def _read_input(path: Path) -> list[dict]:
    """Read all records from input JSONL."""
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def main(input_path: str, output_path: str) -> None:
    input_file = Path(input_path).resolve()
    output_file = Path(output_path).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # 1. Read input records
    records = _read_input(input_file)
    total = len(records)

    # 2. Resume: skip records already written in a previous run
    already_done = _count_existing_lines(output_file)
    if already_done > 0:
        print(f"Resuming: skipping {already_done}/{total} already-processed records")
    remaining = records[already_done:]

    if not remaining:
        print(f"All {total} records already processed. Nothing to do.")
        return

    # 3. Initialize LLM from environment variables (injected by df_run_pipeline
    #    or the platform runner). Wrap with LoggingLLMServing for full call
    #    traceability (writes llm_calls.jsonl to DF_LOG_DIR).
    raw_llm = APILLMServing_request(
        api_url=os.environ.get(
            "DF_API_URL", "https://api.openai.com/v1/chat/completions"
        ),
        model_name=os.environ.get("DF_MODEL_NAME", "gpt-4o-mini"),
        key_name_of_api_key="DF_API_KEY",
        temperature=0.0,
        max_workers=8,
    )
    llm = LoggingLLMServing(raw_llm)

    # 4. Process in batches: N records per LLM call, write after each batch
    succeeded = already_done
    failed = 0
    with output_file.open("a", encoding="utf-8") as out:
        for batch_start in range(0, len(remaining), BATCH_SIZE):
            batch = remaining[batch_start : batch_start + BATCH_SIZE]
            prompts = [str(r.get("problem", "")) for r in batch]

            try:
                responses = llm.generate_from_input(
                    user_inputs=prompts,
                    system_prompt=SYSTEM_PROMPT,
                )
            except Exception as exc:
                api_key = os.environ.get("DF_API_KEY", "")
                err_msg = (
                    str(exc).replace(api_key, "<redacted>") if api_key else str(exc)
                )
                print(
                    f"[ERROR] batch at offset {batch_start}: {err_msg[:200]}",
                    file=sys.stderr,
                )
                failed += len(batch)
                continue

            for record, response in zip(batch, responses):
                if response is None:
                    failed += 1
                    continue
                record["cot"] = response
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                succeeded += 1
            out.flush()

            processed = batch_start + len(batch)
            print(
                f"Progress: {processed}/{len(remaining)} processed, "
                f"{succeeded}/{total} succeeded, {failed} failed"
            )

    print(
        f"Done. total={total} succeeded={succeeded} failed={failed} "
        f"output={output_file}"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input.jsonl> [output.jsonl]", file=sys.stderr)
        sys.exit(1)
    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "processed.jsonl"
    main(input_path, output_path)
