#!/usr/bin/env python3
"""Validate canonical Pyromind text or vision training JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal


SchemaName = Literal["text", "vision"]
TEXT_FIELDS = {"id", "system_prompt", "user_prompt", "gt"}
VISION_FIELDS = {
    "id",
    "image_path",
    "images",
    "system_prompt",
    "user_prompt",
    "gt",
}


def _nonempty_string(row: dict[str, Any], field: str, line_number: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"line {line_number}: {field} must be a non-empty string")
    return value


def _validate_relative_path(value: str, line_number: int) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ValueError(
            f"line {line_number}: image paths must be relative POSIX paths"
        )


def _validate_gt(value: str, line_number: int) -> None:
    if value.count("<think>") != 1 or value.count("</think>") != 1:
        raise ValueError(
            f"line {line_number}: gt must contain exactly one <think> block"
        )
    if value.count("<answer>") != 1 or value.count("</answer>") != 1:
        raise ValueError(
            f"line {line_number}: gt must contain exactly one <answer> block"
        )
    think_start = value.index("<think>") + len("<think>")
    think_end = value.index("</think>")
    answer_start = value.index("<answer>") + len("<answer>")
    answer_end = value.index("</answer>")
    if not value[think_start:think_end].strip():
        raise ValueError(f"line {line_number}: think content must not be empty")
    if not value[answer_start:answer_end].strip():
        raise ValueError(f"line {line_number}: answer content must not be empty")
    if think_end >= answer_start:
        raise ValueError(f"line {line_number}: think block must precede answer block")


def _validate_image_file(path: Path, line_number: int, item: str) -> None:
    if not path.is_file():
        raise ValueError(f"line {line_number}: image does not exist: {item}")
    try:
        from PIL import Image
    except ImportError:
        return
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        raise ValueError(
            f"line {line_number}: image cannot be decoded: {item}"
        ) from exc


def validate_row(
    row: dict[str, Any],
    *,
    schema: SchemaName,
    line_number: int,
    image_root: Path | None = None,
) -> str:
    expected_fields = TEXT_FIELDS if schema == "text" else VISION_FIELDS
    actual_fields = set(row)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        extra = sorted(actual_fields - expected_fields)
        raise ValueError(
            f"line {line_number}: fields do not match {schema} schema; "
            f"missing={missing}, extra={extra}"
        )

    record_id = _nonempty_string(row, "id", line_number)
    _nonempty_string(row, "system_prompt", line_number)
    _nonempty_string(row, "user_prompt", line_number)
    gt = _nonempty_string(row, "gt", line_number)
    if schema == "text":
        return record_id

    image_path = _nonempty_string(row, "image_path", line_number)
    images = row.get("images")
    if not isinstance(images, list) or not images:
        raise ValueError(f"line {line_number}: images must be a non-empty list")
    if any(not isinstance(item, str) or not item for item in images):
        raise ValueError(f"line {line_number}: every images item must be a string")
    if image_path != images[0]:
        raise ValueError(f"line {line_number}: image_path must equal images[0]")
    for item in images:
        _validate_relative_path(item, line_number)
        if image_root is not None:
            _validate_image_file(image_root / item, line_number, item)
    _validate_gt(gt, line_number)
    return record_id


def validate_jsonl(
    path: Path,
    *,
    schema: SchemaName,
    image_root: Path | None = None,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"output JSONL does not exist: {path}")
    seen_ids: set[str] = set()
    rows = 0
    with path.open(encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, 1):
            if not raw_line.strip():
                raise ValueError(f"line {line_number}: blank lines are not allowed")
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"line {line_number}: invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number}: row must be a JSON object")
            record_id = validate_row(
                row,
                schema=schema,
                line_number=line_number,
                image_root=image_root,
            )
            if record_id in seen_ids:
                raise ValueError(f"line {line_number}: duplicate id: {record_id}")
            seen_ids.add(record_id)
            rows += 1
    if rows == 0:
        raise ValueError("output JSONL must contain at least one record")
    return {"status": "passed", "schema": schema, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--schema", required=True, choices=("text", "vision"))
    parser.add_argument("--image-root", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    try:
        result = validate_jsonl(
            args.path,
            schema=args.schema,
            image_root=args.image_root,
        )
    except ValueError as exc:
        result = {
            "status": "failed",
            "schema": args.schema,
            "error": str(exc),
        }
        if args.report:
            args.report.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(result, ensure_ascii=False))
        return 1

    if args.report:
        args.report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
