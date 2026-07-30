"""Checkpointable text data-preparation pipeline template.

Usage:
    pipeline.py <input.jsonl> <processed.jsonl> [limit]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from preparation_runtime import (
    Checkpoint,
    RetryingChatClient,
    commit_record,
    load_checkpoint,
    prepare_resume_output,
    write_failure_report,
)


SYSTEM_PROMPT = "You are a helpful assistant."


def source_id(record: dict[str, Any], source_index: int) -> str:
    return str(record.get("id") or f"text-{source_index}")


def build_user_prompt(record: dict[str, Any]) -> str:
    """Customize source-field selection and task instructions here."""

    value = record.get("user_prompt") or record.get("prompt")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source record is missing a non-empty prompt")
    return value.strip()


def build_model_prompt(record: dict[str, Any]) -> str:
    """Customize generation instructions without leaking reference answers."""

    return build_user_prompt(record)


def validate_generated_answer(value: str) -> None:
    if not value.strip():
        raise ValueError("generated answer must not be empty")


def output_row(
    record: dict[str, Any],
    source_index: int,
    answer: str,
) -> dict[str, str]:
    return {
        "id": source_id(record, source_index),
        "system_prompt": SYSTEM_PROMPT,
        "user_prompt": build_user_prompt(record),
        "gt": answer.strip(),
    }


def read_records(path: Path, limit: int | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                raise ValueError(f"line {line_number}: blank lines are not allowed")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number}: record must be an object")
            records.append(value)
            if limit is not None and len(records) >= limit:
                break
    return records


def run(input_path: str, output_path: str, limit: int | None = None) -> None:
    source = Path(input_path).resolve()
    output = Path(output_path).resolve()
    state_dir = Path(os.environ.get("DF_STATE_DIR", output.parent)).resolve()
    resumed = os.environ.get("DF_RESUME") == "1"
    checkpoint = load_checkpoint(state_dir) if resumed else Checkpoint()
    if resumed:
        prepare_resume_output(output, checkpoint)
    else:
        output.unlink(missing_ok=True)
        for name in (
            "checkpoint.json",
            "failure.json",
            "validation.json",
            "llm_calls.jsonl",
            "report.json",
        ):
            (state_dir / name).unlink(missing_ok=True)

    records = read_records(source, limit)
    client = RetryingChatClient()
    for source_index in range(checkpoint.next_source_index, len(records)):
        record = records[source_index]
        attempts = 0
        try:
            answer, attempts = client.generate(
                prompt=build_model_prompt(record),
                validate=validate_generated_answer,
            )
            commit_record(
                output_path=output,
                state_dir=state_dir,
                source_index=source_index,
                row=output_row(record, source_index, answer),
            )
        except Exception as exc:
            write_failure_report(
                state_dir=state_dir,
                source_index=source_index,
                source_id=source_id(record, source_index),
                source_path=str(source),
                stage="text_generation",
                error=exc,
                attempts=attempts,
                retriable=False,
            )
            raise


if __name__ == "__main__":
    if len(sys.argv) not in {3, 4}:
        raise SystemExit(
            "usage: text_pipeline.py <input.jsonl> <processed.jsonl> [limit]"
        )
    run(
        sys.argv[1],
        sys.argv[2],
        int(sys.argv[3]) if len(sys.argv) == 4 else None,
    )
