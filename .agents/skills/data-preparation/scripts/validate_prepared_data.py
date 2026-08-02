#!/usr/bin/env python3
"""Validate canonical Pyromind data-preparation JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal


SchemaName = Literal[
    "text",
    "vision",
    "multiturn",
    "function_call",
    "quality_evaluation",
    "text2sql",
]
SCHEMA_CHOICES = (
    "text",
    "vision",
    "multiturn",
    "function_call",
    "quality_evaluation",
    "text2sql",
)
TEXT_FIELDS = {"id", "system_prompt", "user_prompt", "gt"}
VISION_FIELDS = {
    "id",
    "image_path",
    "images",
    "system_prompt",
    "user_prompt",
    "gt",
}
MULTITURN_FIELDS = {"id", "messages"}
FUNCTION_CALL_FIELDS = {"id", "tools", "messages"}
QUALITY_EVALUATION_FIELDS = {"id", "subject", "evaluation"}
TEXT2SQL_FIELDS = {"id", "question", "database", "evidence", "sql"}
QUALITY_SUBJECT_TYPES = {
    "text",
    "instruction_response",
    "conversation",
    "function_call",
    "text2sql",
}
QUALITY_DECISIONS = {"keep", "rewrite", "drop"}
FORBIDDEN_AUDIT_FIELDS = {
    "source_path",
    "model",
    "usage",
    "elapsed_sec",
    "reasoning_content",
    "anthropic_response",
}


def _expect_exact_fields(
    value: dict[str, Any],
    expected: set[str],
    *,
    line_number: int,
    label: str,
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    raise ValueError(
        f"line {line_number}: fields do not match {label}; "
        f"missing={missing}, extra={extra}"
    )


def _nonempty_string_value(value: Any, *, line_number: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"line {line_number}: {label} must be a non-empty string")
    return value


def _nonempty_string(
    row: dict[str, Any],
    field: str,
    line_number: int,
) -> str:
    return _nonempty_string_value(
        row.get(field),
        line_number=line_number,
        label=field,
    )


def _integer_score(value: Any, *, line_number: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise ValueError(f"line {line_number}: {label} must be an integer from 1 to 5")
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


def _validate_text_or_vision(
    row: dict[str, Any],
    *,
    schema: SchemaName,
    line_number: int,
    image_root: Path | None,
) -> str:
    expected_fields = TEXT_FIELDS if schema == "text" else VISION_FIELDS
    _expect_exact_fields(
        row,
        expected_fields,
        line_number=line_number,
        label=f"{schema} schema",
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


def _validate_multiturn_message(
    message: Any,
    *,
    line_number: int,
    index: int,
) -> str:
    if not isinstance(message, dict):
        raise ValueError(f"line {line_number}: messages[{index}] must be an object")
    _expect_exact_fields(
        message,
        {"role", "content"},
        line_number=line_number,
        label=f"messages[{index}]",
    )
    role = message.get("role")
    if role not in {"system", "user", "assistant"}:
        raise ValueError(
            f"line {line_number}: messages[{index}].role is invalid: {role!r}"
        )
    _nonempty_string_value(
        message.get("content"),
        line_number=line_number,
        label=f"messages[{index}].content",
    )
    return role


def _validate_multiturn(row: dict[str, Any], *, line_number: int) -> str:
    _expect_exact_fields(
        row,
        MULTITURN_FIELDS,
        line_number=line_number,
        label="multiturn schema",
    )
    record_id = _nonempty_string(row, "id", line_number)
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"line {line_number}: messages must be a non-empty list")
    roles = [
        _validate_multiturn_message(
            message,
            line_number=line_number,
            index=index,
        )
        for index, message in enumerate(messages)
    ]
    start = 1 if roles[0] == "system" else 0
    if "system" in roles[start:]:
        raise ValueError(
            f"line {line_number}: system message is only allowed at the beginning"
        )
    conversation_roles = roles[start:]
    if len(conversation_roles) < 2:
        raise ValueError(
            f"line {line_number}: multiturn data needs at least one user/assistant pair"
        )
    expected = [
        "user" if index % 2 == 0 else "assistant"
        for index in range(len(conversation_roles))
    ]
    if conversation_roles != expected or conversation_roles[-1] != "assistant":
        raise ValueError(
            f"line {line_number}: messages must alternate user/assistant "
            "and end with assistant"
        )
    return record_id


def _validate_function_definition(
    tool: Any,
    *,
    line_number: int,
    index: int,
) -> str:
    if not isinstance(tool, dict):
        raise ValueError(f"line {line_number}: tools[{index}] must be an object")
    _expect_exact_fields(
        tool,
        {"type", "function"},
        line_number=line_number,
        label=f"tools[{index}]",
    )
    if tool.get("type") != "function":
        raise ValueError(f"line {line_number}: tools[{index}].type must be 'function'")
    function = tool.get("function")
    if not isinstance(function, dict):
        raise ValueError(
            f"line {line_number}: tools[{index}].function must be an object"
        )
    _expect_exact_fields(
        function,
        {"name", "description", "parameters"},
        line_number=line_number,
        label=f"tools[{index}].function",
    )
    name = _nonempty_string_value(
        function.get("name"),
        line_number=line_number,
        label=f"tools[{index}].function.name",
    )
    _nonempty_string_value(
        function.get("description"),
        line_number=line_number,
        label=f"tools[{index}].function.description",
    )
    parameters = function.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ValueError(
            f"line {line_number}: tools[{index}].function.parameters "
            "must be a JSON object schema"
        )
    properties = parameters.get("properties")
    required = parameters.get("required", [])
    if not isinstance(properties, dict):
        raise ValueError(
            f"line {line_number}: tools[{index}].function.parameters.properties "
            "must be an object"
        )
    if not isinstance(required, list) or any(
        not isinstance(item, str) for item in required
    ):
        raise ValueError(
            f"line {line_number}: tools[{index}].function.parameters.required "
            "must be a string list"
        )
    if not set(required).issubset(properties):
        raise ValueError(
            f"line {line_number}: tools[{index}] required parameters "
            "must exist in properties"
        )
    if parameters.get("additionalProperties") is not False:
        raise ValueError(
            f"line {line_number}: tools[{index}].function.parameters."
            "additionalProperties must be false"
        )
    return name


def _validate_tool_call(
    tool_call: Any,
    *,
    line_number: int,
    message_index: int,
    call_index: int,
    tool_names: set[str],
) -> str:
    label = f"messages[{message_index}].tool_calls[{call_index}]"
    if not isinstance(tool_call, dict):
        raise ValueError(f"line {line_number}: {label} must be an object")
    _expect_exact_fields(
        tool_call,
        {"id", "type", "function"},
        line_number=line_number,
        label=label,
    )
    call_id = _nonempty_string_value(
        tool_call.get("id"),
        line_number=line_number,
        label=f"{label}.id",
    )
    if tool_call.get("type") != "function":
        raise ValueError(f"line {line_number}: {label}.type must be 'function'")
    function = tool_call.get("function")
    if not isinstance(function, dict):
        raise ValueError(f"line {line_number}: {label}.function must be an object")
    _expect_exact_fields(
        function,
        {"name", "arguments"},
        line_number=line_number,
        label=f"{label}.function",
    )
    name = _nonempty_string_value(
        function.get("name"),
        line_number=line_number,
        label=f"{label}.function.name",
    )
    if name not in tool_names:
        raise ValueError(
            f"line {line_number}: {label}.function.name is not declared in tools"
        )
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise ValueError(
            f"line {line_number}: {label}.function.arguments must be a JSON string"
        )
    try:
        parsed_arguments = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"line {line_number}: {label}.function.arguments is invalid JSON"
        ) from exc
    if not isinstance(parsed_arguments, dict):
        raise ValueError(
            f"line {line_number}: {label}.function.arguments must decode to an object"
        )
    return call_id


def _validate_function_call(row: dict[str, Any], *, line_number: int) -> str:
    _expect_exact_fields(
        row,
        FUNCTION_CALL_FIELDS,
        line_number=line_number,
        label="function_call schema",
    )
    record_id = _nonempty_string(row, "id", line_number)
    tools = row.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError(f"line {line_number}: tools must be a non-empty list")
    tool_names = [
        _validate_function_definition(
            tool,
            line_number=line_number,
            index=index,
        )
        for index, tool in enumerate(tools)
    ]
    if len(tool_names) != len(set(tool_names)):
        raise ValueError(f"line {line_number}: function names must be unique")

    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"line {line_number}: messages must be a non-empty list")
    declared_calls: set[str] = set()
    returned_calls: list[str] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"line {line_number}: messages[{index}] must be an object")
        role = message.get("role")
        if role == "assistant" and "tool_calls" in message:
            _expect_exact_fields(
                message,
                {"role", "content", "tool_calls"},
                line_number=line_number,
                label=f"messages[{index}]",
            )
            if not isinstance(message.get("content"), str):
                raise ValueError(
                    f"line {line_number}: messages[{index}].content must be a string"
                )
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list) or not tool_calls:
                raise ValueError(
                    f"line {line_number}: messages[{index}].tool_calls "
                    "must be a non-empty list"
                )
            for call_index, tool_call in enumerate(tool_calls):
                call_id = _validate_tool_call(
                    tool_call,
                    line_number=line_number,
                    message_index=index,
                    call_index=call_index,
                    tool_names=set(tool_names),
                )
                if call_id in declared_calls:
                    raise ValueError(
                        f"line {line_number}: duplicate tool call id: {call_id}"
                    )
                declared_calls.add(call_id)
        elif role == "tool":
            _expect_exact_fields(
                message,
                {"role", "tool_call_id", "content"},
                line_number=line_number,
                label=f"messages[{index}]",
            )
            call_id = _nonempty_string_value(
                message.get("tool_call_id"),
                line_number=line_number,
                label=f"messages[{index}].tool_call_id",
            )
            if not isinstance(message.get("content"), str):
                raise ValueError(
                    f"line {line_number}: messages[{index}].content must be a string"
                )
            returned_calls.append(call_id)
        else:
            _expect_exact_fields(
                message,
                {"role", "content"},
                line_number=line_number,
                label=f"messages[{index}]",
            )
            if role not in {"system", "user", "assistant"}:
                raise ValueError(
                    f"line {line_number}: messages[{index}].role is invalid: {role!r}"
                )
            _nonempty_string_value(
                message.get("content"),
                line_number=line_number,
                label=f"messages[{index}].content",
            )
            if role == "system" and index != 0:
                raise ValueError(
                    f"line {line_number}: system message is only allowed first"
                )

    if not declared_calls:
        raise ValueError(f"line {line_number}: function_call data needs a tool call")
    if set(returned_calls) != declared_calls or len(returned_calls) != len(
        declared_calls
    ):
        raise ValueError(
            f"line {line_number}: every tool call must have exactly one tool response"
        )
    final_message = messages[-1]
    if (
        not isinstance(final_message, dict)
        or final_message.get("role") != "assistant"
        or "tool_calls" in final_message
        or not isinstance(final_message.get("content"), str)
        or not final_message["content"].strip()
    ):
        raise ValueError(
            f"line {line_number}: function_call trajectory must end "
            "with a final assistant response"
        )
    return record_id


def _validate_quality_evaluation(
    row: dict[str, Any],
    *,
    line_number: int,
) -> str:
    _expect_exact_fields(
        row,
        QUALITY_EVALUATION_FIELDS,
        line_number=line_number,
        label="quality_evaluation schema",
    )
    record_id = _nonempty_string(row, "id", line_number)
    subject = row.get("subject")
    if not isinstance(subject, dict):
        raise ValueError(f"line {line_number}: subject must be an object")
    subject_type = subject.get("type")
    if subject_type not in QUALITY_SUBJECT_TYPES:
        raise ValueError(
            f"line {line_number}: subject.type must be one of "
            f"{sorted(QUALITY_SUBJECT_TYPES)}"
        )
    if len(subject) < 2:
        raise ValueError(
            f"line {line_number}: subject must contain the data being evaluated"
        )
    leaked = sorted(set(subject) & FORBIDDEN_AUDIT_FIELDS)
    if leaked:
        raise ValueError(
            f"line {line_number}: subject contains audit fields: {leaked}"
        )

    evaluation = row.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError(f"line {line_number}: evaluation must be an object")
    _expect_exact_fields(
        evaluation,
        {"overall_score", "decision", "dimensions", "issues"},
        line_number=line_number,
        label="evaluation",
    )
    _integer_score(
        evaluation.get("overall_score"),
        line_number=line_number,
        label="evaluation.overall_score",
    )
    if evaluation.get("decision") not in QUALITY_DECISIONS:
        raise ValueError(
            f"line {line_number}: evaluation.decision must be keep/rewrite/drop"
        )
    dimensions = evaluation.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise ValueError(
            f"line {line_number}: evaluation.dimensions must be a non-empty list"
        )
    dimension_names: list[str] = []
    for index, dimension in enumerate(dimensions):
        if not isinstance(dimension, dict):
            raise ValueError(
                f"line {line_number}: evaluation.dimensions[{index}] "
                "must be an object"
            )
        _expect_exact_fields(
            dimension,
            {"name", "score", "reason"},
            line_number=line_number,
            label=f"evaluation.dimensions[{index}]",
        )
        dimension_names.append(
            _nonempty_string_value(
                dimension.get("name"),
                line_number=line_number,
                label=f"evaluation.dimensions[{index}].name",
            )
        )
        _integer_score(
            dimension.get("score"),
            line_number=line_number,
            label=f"evaluation.dimensions[{index}].score",
        )
        _nonempty_string_value(
            dimension.get("reason"),
            line_number=line_number,
            label=f"evaluation.dimensions[{index}].reason",
        )
    if len(dimension_names) != len(set(dimension_names)):
        raise ValueError(f"line {line_number}: dimension names must be unique")

    issues = evaluation.get("issues")
    if not isinstance(issues, list):
        raise ValueError(f"line {line_number}: evaluation.issues must be a list")
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise ValueError(
                f"line {line_number}: evaluation.issues[{index}] must be an object"
            )
        _expect_exact_fields(
            issue,
            {"type", "severity", "message"},
            line_number=line_number,
            label=f"evaluation.issues[{index}]",
        )
        for field in ("type", "severity", "message"):
            _nonempty_string_value(
                issue.get(field),
                line_number=line_number,
                label=f"evaluation.issues[{index}].{field}",
            )
    return record_id


def _validate_text2sql(row: dict[str, Any], *, line_number: int) -> str:
    _expect_exact_fields(
        row,
        TEXT2SQL_FIELDS,
        line_number=line_number,
        label="text2sql schema",
    )
    record_id = _nonempty_string(row, "id", line_number)
    _nonempty_string(row, "question", line_number)
    if not isinstance(row.get("evidence"), str):
        raise ValueError(f"line {line_number}: evidence must be a string")
    _nonempty_string(row, "sql", line_number)

    database = row.get("database")
    if not isinstance(database, dict):
        raise ValueError(f"line {line_number}: database must be an object")
    _expect_exact_fields(
        database,
        {"id", "dialect", "schema"},
        line_number=line_number,
        label="database",
    )
    _nonempty_string_value(
        database.get("id"),
        line_number=line_number,
        label="database.id",
    )
    _nonempty_string_value(
        database.get("dialect"),
        line_number=line_number,
        label="database.dialect",
    )
    db_schema = database.get("schema")
    if not isinstance(db_schema, dict):
        raise ValueError(f"line {line_number}: database.schema must be an object")
    _expect_exact_fields(
        db_schema,
        {"tables", "foreign_keys"},
        line_number=line_number,
        label="database.schema",
    )
    tables = db_schema.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError(
            f"line {line_number}: database.schema.tables must be a non-empty list"
        )
    known_columns: set[str] = set()
    table_names: list[str] = []
    for table_index, table in enumerate(tables):
        label = f"database.schema.tables[{table_index}]"
        if not isinstance(table, dict):
            raise ValueError(f"line {line_number}: {label} must be an object")
        _expect_exact_fields(
            table,
            {"name", "columns", "primary_key"},
            line_number=line_number,
            label=label,
        )
        table_name = _nonempty_string_value(
            table.get("name"),
            line_number=line_number,
            label=f"{label}.name",
        )
        table_names.append(table_name)
        columns = table.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValueError(
                f"line {line_number}: {label}.columns must be a non-empty list"
            )
        column_names: list[str] = []
        for column_index, column in enumerate(columns):
            column_label = f"{label}.columns[{column_index}]"
            if not isinstance(column, dict):
                raise ValueError(
                    f"line {line_number}: {column_label} must be an object"
                )
            _expect_exact_fields(
                column,
                {"name", "type", "nullable"},
                line_number=line_number,
                label=column_label,
            )
            column_name = _nonempty_string_value(
                column.get("name"),
                line_number=line_number,
                label=f"{column_label}.name",
            )
            _nonempty_string_value(
                column.get("type"),
                line_number=line_number,
                label=f"{column_label}.type",
            )
            if not isinstance(column.get("nullable"), bool):
                raise ValueError(
                    f"line {line_number}: {column_label}.nullable must be boolean"
                )
            column_names.append(column_name)
            known_columns.add(f"{table_name}.{column_name}")
        if len(column_names) != len(set(column_names)):
            raise ValueError(f"line {line_number}: duplicate column in {table_name}")
        primary_key = table.get("primary_key")
        if not isinstance(primary_key, list) or any(
            not isinstance(item, str) for item in primary_key
        ):
            raise ValueError(
                f"line {line_number}: {label}.primary_key must be a string list"
            )
        if not set(primary_key).issubset(column_names):
            raise ValueError(
                f"line {line_number}: {label}.primary_key references unknown columns"
            )
    if len(table_names) != len(set(table_names)):
        raise ValueError(f"line {line_number}: database table names must be unique")

    foreign_keys = db_schema.get("foreign_keys")
    if not isinstance(foreign_keys, list):
        raise ValueError(
            f"line {line_number}: database.schema.foreign_keys must be a list"
        )
    for index, foreign_key in enumerate(foreign_keys):
        label = f"database.schema.foreign_keys[{index}]"
        if not isinstance(foreign_key, dict):
            raise ValueError(f"line {line_number}: {label} must be an object")
        _expect_exact_fields(
            foreign_key,
            {"from", "to"},
            line_number=line_number,
            label=label,
        )
        source = _nonempty_string_value(
            foreign_key.get("from"),
            line_number=line_number,
            label=f"{label}.from",
        )
        target = _nonempty_string_value(
            foreign_key.get("to"),
            line_number=line_number,
            label=f"{label}.to",
        )
        if source not in known_columns or target not in known_columns:
            raise ValueError(
                f"line {line_number}: {label} references an unknown table column"
            )
    return record_id


def validate_row(
    row: dict[str, Any],
    *,
    schema: SchemaName,
    line_number: int,
    image_root: Path | None = None,
) -> str:
    if schema in {"text", "vision"}:
        return _validate_text_or_vision(
            row,
            schema=schema,
            line_number=line_number,
            image_root=image_root,
        )
    if schema == "multiturn":
        return _validate_multiturn(row, line_number=line_number)
    if schema == "function_call":
        return _validate_function_call(row, line_number=line_number)
    if schema == "quality_evaluation":
        return _validate_quality_evaluation(row, line_number=line_number)
    return _validate_text2sql(row, line_number=line_number)


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
    parser.add_argument("--schema", required=True, choices=SCHEMA_CHOICES)
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
