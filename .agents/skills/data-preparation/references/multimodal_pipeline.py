"""Generic DataFlow multi-image semantic labeling pipeline template.

Copy this file to the conversation workspace and customize only the constants
and small task hooks below. It deliberately calls DataFlow's existing VLM
serving directly instead of defining a new operator.

Incremental mode: processes records in small batches and writes each result
immediately to disk. Supports automatic resume on restart (skips records
already present in the output file).

Usage:
    pipeline.py <manifest.jsonl> <processed.jsonl> [limit]
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


# Replace these values for the current labeling task. If the user supplied
# natural-language guidance or relevant rule files, condense them into
# SYSTEM_PROMPT instead of copying entire documents.
SYSTEM_PROMPT = """Describe the labeling task, image roles, judgment guidance,
and required output here."""
TRAINING_SYSTEM_PROMPT = """Describe the task and the required answer format for
the training-time assistant here."""
TRAINING_PROMPT = ""
REFERENCE_INPUT_FIELDS: tuple[str, ...] = ()
PROTECTED_REFERENCE_FIELDS: tuple[str, ...] = ()
OUTPUT_JSON_SCHEMA: dict[str, Any] | None = None
REASONING_FIELD = "reasoning"
ANSWER_FIELD = "answer"
ANSWER_IS_JSON = False
FIELD_POLICY_RATIONALE = (
    "Resolved from the user's request, inspected fields, and available guidance."
)


def _safe_error(exc: BaseException) -> str:
    message = str(exc)
    api_key = os.environ.get("DF_API_KEY")
    if api_key:
        message = message.replace(api_key, "<redacted>")
    return message[:2000]


def _load_vlm_class():
    serving_dir = Path(dataflow.__file__).parent / "serving"
    package = types.ModuleType("dataflow.serving")
    package.__path__ = [str(serving_dir)]
    sys.modules["dataflow.serving"] = package
    module_name = "dataflow.serving.api_vlm_serving_openai"
    spec = importlib.util.spec_from_file_location(
        module_name,
        serving_dir / "api_vlm_serving_openai.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.APIVLMServing_openai


def _read_manifest(path: Path, limit: int | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"line {line_number}: record must be an object")
            records.append(record)
            if limit is not None and len(records) >= limit:
                break
    return records


def _normalize_record(record: dict[str, Any], manifest: Path) -> dict[str, Any]:
    sample_id = str(record.get("sample_id", "")).strip()
    prompt = str(record.get("prompt", "")).strip()
    image_paths = record.get("image_paths")
    if not sample_id or not prompt:
        raise ValueError("sample_id and prompt must be non-empty")
    if not isinstance(image_paths, list) or not image_paths:
        raise ValueError("image_paths must be a non-empty list")

    resolved: list[str] = []
    for raw_path in image_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("each image path must be a non-empty string")
        path = Path(raw_path)
        path = (
            path.resolve() if path.is_absolute() else (manifest.parent / path).resolve()
        )
        if not path.is_file():
            raise ValueError(f"missing image: {raw_path}")
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise ValueError(f"unsupported image format: {raw_path}")
        resolved.append(str(path))

    raw_labels = record.get("image_labels")
    if raw_labels is None:
        labels = [f"Image {index}" for index in range(1, len(resolved) + 1)]
    elif isinstance(raw_labels, list) and len(raw_labels) == len(resolved):
        labels = [str(label) for label in raw_labels]
    else:
        raise ValueError("image_labels must match image_paths length")

    references = record.get("reference_annotations") or {}
    if not isinstance(references, dict):
        raise ValueError("reference_annotations must be an object")
    normalized = dict(record)
    normalized["_resolved_image_paths"] = resolved
    normalized["_resolved_image_labels"] = labels
    normalized["reference_annotations"] = references
    return normalized


def build_user_prompt(record: dict[str, Any]) -> str:
    """Build sample-specific model input without changing the manifest protocol."""

    parts = [str(record["prompt"])]
    references = record["reference_annotations"]
    selected = {
        field: references[field]
        for field in REFERENCE_INPUT_FIELDS
        if field in references
    }
    if selected:
        parts.append(
            "Existing reference annotations:\n"
            + json.dumps(selected, ensure_ascii=False)
        )
    return "\n\n".join(parts)


def parse_generated_annotations(raw_response: str) -> dict[str, Any]:
    """Customize this hook when the task uses a non-object response."""

    if OUTPUT_JSON_SCHEMA is None:
        return {"generated_text": raw_response}
    value = json.loads(raw_response)
    if not isinstance(value, dict):
        raise ValueError("VLM response must be a JSON object")
    return value


def _stringify_response_part(value: Any, field: str) -> str:
    if isinstance(value, str):
        text = value.strip()
    elif value is None:
        text = ""
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if not text:
        raise ValueError(f"{field} must be non-empty")
    return text


def validate_training_response(response: str) -> None:
    required_tags = ("<think>", "</think>", "<answer>", "</answer>")
    if any(response.count(tag) != 1 for tag in required_tags):
        raise ValueError("training_response must contain each required tag once")
    separator = "</think>\n\n<answer>"
    if not response.startswith("<think>") or not response.endswith("</answer>"):
        raise ValueError("training_response has invalid tag order")
    body = response[len("<think>") : -len("</answer>")]
    if separator not in body:
        raise ValueError("training_response has invalid tag order")
    think, answer = body.split(separator, maxsplit=1)
    if not think.strip() or not answer.strip():
        raise ValueError("training_response fields must be non-empty")
    if ANSWER_IS_JSON:
        json.loads(answer)


def assemble_training_response(
    record: dict[str, Any],
    generated: dict[str, Any],
) -> str:
    """Build the tagged response from task-selected annotation fields."""

    references = record["reference_annotations"]
    protected = {
        field: references[field]
        for field in PROTECTED_REFERENCE_FIELDS
        if field in references
    }
    annotations = {**generated, **protected}
    reasoning = _stringify_response_part(
        annotations.get(REASONING_FIELD),
        REASONING_FIELD,
    )
    answer = _stringify_response_part(
        annotations.get(ANSWER_FIELD),
        ANSWER_FIELD,
    )
    response = f"<think>{reasoning}</think>\n\n<answer>{answer}</answer>"
    validate_training_response(response)
    return response


BATCH_SIZE = 4


def _generate_batch(
    vlm: Any,
    records: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Call VLM for a small batch with recursive error isolation."""
    if not records:
        return {}, {}
    try:
        responses = vlm.generate_from_input_multi_images(
            [record["_resolved_image_paths"] for record in records],
            [record["_resolved_image_labels"] for record in records],
            system_prompt=SYSTEM_PROMPT,
            user_prompts=[build_user_prompt(record) for record in records],
            json_schema=OUTPUT_JSON_SCHEMA,
        )
    except Exception as exc:
        if len(records) == 1:
            return {}, {str(records[0]["sample_id"]): _safe_error(exc)}
        middle = len(records) // 2
        left_ok, left_failed = _generate_batch(vlm, records[:middle])
        right_ok, right_failed = _generate_batch(vlm, records[middle:])
        return {**left_ok, **right_ok}, {**left_failed, **right_failed}
    return {
        str(record["sample_id"]): response
        for record, response in zip(records, responses, strict=True)
    }, {}


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


def run(
    input_path: str,
    output_path: str,
    limit: int | None = None,
) -> None:
    manifest = Path(input_path).resolve()
    output = Path(output_path).resolve()
    report = output.with_suffix(".report.json")
    output.parent.mkdir(parents=True, exist_ok=True)

    raw_records = _read_manifest(manifest, limit)
    records: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    for raw_record in raw_records:
        sample_id = str(raw_record.get("sample_id", "<missing>"))
        try:
            records.append(_normalize_record(raw_record, manifest))
        except Exception as exc:
            failures[sample_id] = _safe_error(exc)

    total = len(records)

    # Resume: skip records already written in a previous run
    already_done = _count_existing_lines(output)
    if already_done > 0:
        print(f"Resuming: skipping {already_done}/{total} already-processed records")
    remaining = records[already_done:]

    if not remaining:
        print(f"All {total} records already processed. Nothing to do.")
        return

    # Initialize VLM
    APIVLMServing = _load_vlm_class()
    vlm = APIVLMServing(
        api_url=os.environ["DF_API_BASE_URL"],
        key_name_of_api_key="DF_API_KEY",
        model_name=os.environ["DF_MODEL_NAME"],
    )

    succeeded = already_done
    generated_field_names: set[str] = set()
    try:
        with output.open("a", encoding="utf-8") as handle:
            for batch_start in range(0, len(remaining), BATCH_SIZE):
                batch = remaining[batch_start : batch_start + BATCH_SIZE]
                generated, batch_failures = _generate_batch(vlm, batch)
                failures.update(batch_failures)

                for record in batch:
                    sample_id = str(record["sample_id"])
                    raw_response = generated.get(sample_id)
                    if raw_response is None:
                        continue
                    try:
                        annotations = parse_generated_annotations(raw_response)
                        generated_field_names.update(annotations)
                        result = {
                            key: value
                            for key, value in record.items()
                            if not key.startswith("_")
                        }
                        result["generated_annotations"] = annotations
                        result["training_system_prompt"] = TRAINING_SYSTEM_PROMPT
                        result["training_prompt"] = TRAINING_PROMPT or record["prompt"]
                        result["training_response"] = assemble_training_response(
                            record,
                            annotations,
                        )
                        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                        handle.flush()
                        succeeded += 1
                    except Exception as exc:
                        failures[sample_id] = _safe_error(exc)

                processed_so_far = batch_start + len(batch) + already_done
                if processed_so_far % 50 < BATCH_SIZE:
                    print(
                        f"Progress: {succeeded}/{total} succeeded, "
                        f"{len(failures)} failed"
                    )
    finally:
        vlm.cleanup()

    print(
        f"Done. total={total} succeeded={succeeded} "
        f"failed={len(failures)} output={output}"
    )

    report.write_text(
        json.dumps(
            {
                "read": len(raw_records),
                "succeeded": succeeded,
                "failed": len(failures),
                "failures": failures,
                "field_policy": {
                    "model_reads": {
                        "images": True,
                        "sample_prompt": True,
                        "reference_annotation_fields": list(REFERENCE_INPUT_FIELDS),
                        "metadata": False,
                    },
                    "generated_fields": sorted(generated_field_names),
                    "preserved_reference_fields": list(PROTECTED_REFERENCE_FIELDS),
                    "training_prompt": (
                        "task-level TRAINING_PROMPT"
                        if TRAINING_PROMPT
                        else "sample prompt"
                    ),
                    "training_response": (
                        f"<think> from {REASONING_FIELD}; "
                        f"<answer> from {ANSWER_FIELD}; protected reference "
                        "values win when fields overlap"
                    ),
                    "rationale": FIELD_POLICY_RATIONALE,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    if len(sys.argv) not in {3, 4}:
        raise SystemExit(
            "usage: multimodal_pipeline.py <manifest.jsonl> <processed.jsonl> [limit]"
        )
    run(
        sys.argv[1],
        sys.argv[2],
        int(sys.argv[3]) if len(sys.argv) == 4 else None,
    )
