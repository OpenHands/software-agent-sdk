"""Checkpointable text data-preparation pipeline template.

Usage:
    pipeline.py <input.jsonl> <processed.jsonl> [limit]

Records are processed in batches via DataFlow's ``generate_from_input`` (a batch of
prompts fans out across an internal thread pool with built-in retries and timeouts)
instead of one blocking request per record. After each batch, committed records are
checkpointed and a ``progress.json`` snapshot is written for ``df_check_progress``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, TypeGuard

from preparation_runtime import (
    Checkpoint,
    commit_record,
    create_text_serving,
    load_checkpoint,
    prepare_resume_output,
    write_failure_report,
    write_progress,
)


SYSTEM_PROMPT = "You are a helpful assistant."

# Records per generate_from_input batch; the serving fans each batch out across its
# internal thread pool (DF_MAX_WORKERS). Override with DF_BATCH_SIZE.
BATCH_SIZE = int(os.environ.get("DF_BATCH_SIZE", "8"))


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


def validate_generated_answer(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


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
            "progress.json",
        ):
            (state_dir / name).unlink(missing_ok=True)

    records = read_records(source, limit)
    total = len(records)
    baseline = checkpoint.next_source_index
    progress_path = state_dir / "progress.json"
    started_at = time.monotonic()
    serving = create_text_serving()
    succeeded = baseline
    try:
        write_progress(
            progress_path,
            total=total,
            processed=baseline,
            succeeded=baseline,
            failed=0,
            started_at=started_at,
            attempted_this_run=0,
        )
        for batch_start in range(baseline, total, BATCH_SIZE):
            batch = records[batch_start : batch_start + BATCH_SIZE]
            prompts = [build_model_prompt(record) for record in batch]
            responses = serving.generate_from_input(
                user_inputs=prompts,
                system_prompt=SYSTEM_PROMPT,
            )
            for offset, record in enumerate(batch):
                source_index = batch_start + offset
                response = responses[offset] if offset < len(responses) else None
                try:
                    if not validate_generated_answer(response):
                        raise ValueError("generated answer must be a non-empty string")
                    commit_record(
                        output_path=output,
                        state_dir=state_dir,
                        source_index=source_index,
                        row=output_row(record, source_index, response),
                    )
                except Exception as exc:
                    write_failure_report(
                        state_dir=state_dir,
                        source_index=source_index,
                        source_id=source_id(record, source_index),
                        source_path=str(source),
                        stage="text_generation",
                        error=exc,
                        attempts=0,
                        retriable=False,
                    )
                    raise
                succeeded += 1
            batch_end = batch_start + len(batch)
            write_progress(
                progress_path,
                total=total,
                processed=batch_end,
                succeeded=succeeded,
                failed=0,
                started_at=started_at,
                attempted_this_run=batch_end - baseline,
            )
            print(
                f"[text_pipeline] {batch_end}/{total} processed, {succeeded} succeeded",
                flush=True,
            )
        print(
            f"[text_pipeline] done: total={total} succeeded={succeeded} "
            f"output={output}",
            flush=True,
        )
    finally:
        cleanup = getattr(serving, "cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass


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
