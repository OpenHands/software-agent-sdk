"""Tests for per-call prompt token composition estimates."""

import json
from collections.abc import Sequence
from typing import ClassVar
from unittest.mock import patch

import pytest
from litellm.utils import token_counter
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.utils.prompt_composition import (
    compute_prompt_composition,
    responses_payload_to_chat_messages,
)
from openhands.sdk.llm.utils.prompt_composition_report import (
    build_report,
    write_report,
)
from openhands.sdk.tool.schema import Action
from openhands.sdk.tool.tool import ToolDefinition


class _Args(Action):
    param: str


class _MockTool(ToolDefinition[_Args, None]):
    name: ClassVar[str] = "test_tool"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_MockTool"]:
        return [cls(description="A test tool", action_type=_Args)]


def _make_llm(model: str = "gpt-4o") -> LLM:
    return LLM(model=model, api_key=SecretStr("test"), usage_id="test-llm")


def _sample_messages() -> list[Message]:
    return [
        Message(role="system", content=[TextContent(text="You are an agent." * 50)]),
        Message(role="user", content=[TextContent(text="please do the task" * 20)]),
        Message(role="assistant", content=[TextContent(text="working on it" * 20)]),
        Message(
            role="user", content=[TextContent(text="observation: file written" * 20)]
        ),
    ]


def test_compute_prompt_composition_decomposes_into_components():
    formatted = _make_llm().format_messages_for_llm(_sample_messages())
    tools = [
        t.to_openai_tool(add_security_risk_prediction=True) for t in _MockTool.create()
    ]

    composition = compute_prompt_composition(
        model="gpt-4o", messages=formatted, tools=tools
    )

    assert composition is not None
    assert composition.is_estimate
    assert composition.system_prompt_tokens > 0
    assert composition.tool_schema_tokens > 0
    assert composition.history_tokens > 0
    assert composition.latest_message_tokens > 0
    # Buckets are counted independently with per-message framing overhead,
    # so their sum covers the message content of the whole prompt.
    total_messages = token_counter(model="gpt-4o", messages=formatted)
    component_sum = (
        composition.system_prompt_tokens
        + composition.history_tokens
        + composition.latest_message_tokens
    )
    assert component_sum >= total_messages


def test_compute_prompt_composition_single_turn_has_no_history():
    formatted = _make_llm().format_messages_for_llm(_sample_messages()[:2])

    composition = compute_prompt_composition(model="gpt-4o", messages=formatted)

    assert composition is not None
    assert composition.tool_schema_tokens == 0
    assert composition.history_tokens == 0
    assert composition.system_prompt_tokens > 0
    assert composition.latest_message_tokens > 0


def test_compute_prompt_composition_returns_none_when_counter_fails():
    with patch(
        "openhands.sdk.llm.utils.prompt_composition.token_counter",
        side_effect=ValueError("unknown model"),
    ):
        composition = compute_prompt_composition(
            model="not-a-model", messages=[{"role": "user", "content": "hi"}]
        )

    assert composition is None


def test_compute_prompt_composition_skips_all_zero_records():
    """A disabled token counter (all-zero result) must skip the record."""
    with patch(
        "openhands.sdk.llm.utils.prompt_composition.token_counter",
        return_value=0,
    ):
        composition = compute_prompt_composition(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )

    assert composition is None


def test_compute_prompt_composition_tool_schema_tokens_matches_controlled_delta():
    """tool_schema_tokens must equal the marginal cost of adding tools."""
    formatted = _make_llm().format_messages_for_llm(_sample_messages())
    tools = [
        t.to_openai_tool(add_security_risk_prediction=True) for t in _MockTool.create()
    ]

    with_tools = compute_prompt_composition(
        model="gpt-4o", messages=formatted, tools=tools
    )
    without_tools = compute_prompt_composition(model="gpt-4o", messages=formatted)
    assert with_tools is not None and without_tools is not None
    assert without_tools.tool_schema_tokens == 0

    component_sum = (
        with_tools.system_prompt_tokens
        + with_tools.tool_schema_tokens
        + with_tools.history_tokens
        + with_tools.latest_message_tokens
    )
    other_components = (
        without_tools.system_prompt_tokens
        + without_tools.history_tokens
        + without_tools.latest_message_tokens
    )
    # Deliberately pins the joint-counting assumption (tools never enter the
    # message buckets); it is an identity by construction, kept as a tripwire.
    assert component_sum - other_components == with_tools.tool_schema_tokens

    # And the estimate tracks the real marginal cost of the tools closely.
    real_delta = token_counter(
        model="gpt-4o", messages=formatted, tools=tools
    ) - token_counter(model="gpt-4o", messages=formatted)
    assert abs(with_tools.tool_schema_tokens - real_delta) <= 8


def test_responses_payload_to_chat_messages_covers_sent_item_types():
    instructions, input_items = _make_llm("gpt-5-mini").format_messages_for_responses(
        _sample_messages()
    )
    assert instructions is not None

    chat = responses_payload_to_chat_messages(instructions, input_items)

    assert chat[0] == {"role": "system", "content": instructions}
    roles = [m["role"] for m in chat[1:]]
    assert roles == ["user", "assistant", "user"]

    reasoning_chat = responses_payload_to_chat_messages(
        None,
        [
            {
                "type": "reasoning",
                "id": "rid",
                "summary": [{"type": "summary_text", "text": "thinking"}],
                "encrypted_content": "blob",
            },
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "c1",
                "name": "do",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "c1", "output": "done"},
        ],
    )
    assert reasoning_chat[0]["role"] == "assistant"
    assert "blob" in reasoning_chat[0]["content"]
    assert reasoning_chat[1]["tool_calls"][0]["function"]["name"] == "do"
    assert reasoning_chat[2] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "done",
    }

    with pytest.raises(ValueError, match="Unrecognized Responses input item"):
        responses_payload_to_chat_messages(None, [{"type": "mystery"}])


def test_responses_payload_to_chat_messages_subscription_shaped_items():
    """Subscription mode strips the "type" key from message items
    (transform_for_subscription); they must still convert."""
    chat = responses_payload_to_chat_messages(
        None,
        [
            {"role": "user", "content": "system prompt folded in"},
            {"role": "assistant", "content": "working on it"},
        ],
    )

    assert chat == [
        {"role": "user", "content": "system prompt folded in"},
        {"role": "assistant", "content": "working on it"},
    ]


def test_responses_payload_to_chat_messages_image_parts():
    chat = responses_payload_to_chat_messages(
        None,
        [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "what is this?"},
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,AAAA",
                        "detail": "auto",
                    },
                ],
            }
        ],
    )

    assert chat == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AAAA"},
                },
            ],
        }
    ]


def _write_log(run_dir, name: str, payload: dict):
    path = run_dir / name
    path.write_text(json.dumps(payload))
    return path


_OPENAI_TOOL = {
    "type": "function",
    "function": {
        "name": "test_tool",
        "description": "A test tool",
        "parameters": {
            "type": "object",
            "properties": {"param": {"type": "string"}},
            "required": ["param"],
        },
    },
}

_TOOL_DEFINITION_DUMP = {
    "description": "A test tool",
    "action_type": "_Args",
    "kind": "_MockTool",
    "title": "test_tool",
}

# Responses ToolParam as logged on the Responses path: schema fields at the
# top level, no nested "function" key.
_RESPONSES_TOOL = {
    "type": "function",
    "name": "test_tool",
    "description": "A test tool",
    "parameters": {
        "type": "object",
        "properties": {"param": {"type": "string"}},
        "required": ["param"],
    },
}


def _chat_log(
    response_id: str,
    timestamp: float,
    history: list[dict] | None = None,
    tools: list | None = None,
    prompt_tokens: int = 900,
) -> dict:
    messages = [{"role": "system", "content": "You are an agent. " * 50}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": "latest observation " * 10})
    return {
        "context_window": 128000,
        "messages": messages,
        "tools": tools if tools is not None else [_OPENAI_TOOL],
        "kwargs": {},
        "response": {"id": response_id, "model": "gpt-4o"},
        "usage_summary": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 12,
            "reasoning_tokens": 0,
            "cache_read_tokens": 0,
        },
        "cost": 0.001,
        "timestamp": timestamp,
        "latency_sec": 1.5,
    }


def test_report_ingests_chat_logs_in_timestamp_order(tmp_path):
    run = tmp_path / "run1"
    run.mkdir()
    _write_log(run, "gpt-4o-002.000-ab12.json", _chat_log("r2", timestamp=2.0))
    _write_log(run, "gpt-4o-001.000-cd34.json", _chat_log("r1", timestamp=1.0))

    report = build_report(run)

    assert report["skipped"] == []
    assert [row["seq"] for row in report["rows"]] == [0, 1]
    first, _second = report["rows"]
    # seq follows the call timestamp, not the filename order.
    assert first["composition"]["response_id"] == "r1"
    assert first["tool_schema_counted"] is True
    assert first["composition"]["system_prompt_tokens"] > 0
    assert first["composition"]["tool_schema_tokens"] > 0
    assert first["composition"]["latest_message_tokens"] > 0
    assert first["usage"]["prompt_tokens"] == 900
    assert first["latency_s"] == 1.5

    summary = report["summary"]
    assert summary["calls"] == 2
    assert summary["calls_with_uncountable_tool_schemas"] == 0
    assert summary["avg"]["system_prompt_tokens"] > 0
    assert summary["est_provider_ratio_calls"] == 2
    assert summary["est_provider_median_ratio"] is not None


def test_report_ingests_responses_log(tmp_path):
    run = tmp_path / "run1"
    run.mkdir()
    _write_log(
        run,
        "gpt-5-mini-001.000-ab12.json",
        {
            "context_window": 200000,
            "llm_path": "responses",
            "instructions": "You are an agent. " * 50,
            "input": [
                {"type": "message", "role": "user", "content": "do the task " * 10},
                {
                    "type": "function_call",
                    "id": "fc1",
                    "call_id": "c1",
                    "name": "test_tool",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": "done " * 30,
                },
            ],
            "tools": [_RESPONSES_TOOL],
            "kwargs": {},
            "response": {"id": "resp-1", "model": "gpt-5-mini"},
            "usage_summary": {
                "prompt_tokens": 800,
                "completion_tokens": 8,
                "reasoning_tokens": 3,
                "cache_read_tokens": 0,
            },
            "cost": 0.002,
            "timestamp": 1.0,
            "latency_sec": 2.1,
        },
    )

    report = build_report(run)

    assert report["skipped"] == []
    (row,) = report["rows"]
    # instructions become the system bucket; input items fill history/latest.
    assert row["composition"]["system_prompt_tokens"] > 0
    assert row["composition"]["tool_schema_tokens"] > 0
    assert row["composition"]["history_tokens"] > 0
    assert row["tool_schema_counted"] is True
    assert row["usage"]["response_id"] == "resp-1"


def test_report_mock_tools_log_counts_schemas_once(tmp_path):
    """With raw_messages present, schemas live in the prompt text: the tool
    bucket stays 0 (no double count) while they inflate the system bucket."""
    run = tmp_path / "run1"
    run.mkdir()
    raw_messages = [
        {"role": "system", "content": "You are an agent. " * 50},
        {"role": "user", "content": "do the task"},
    ]
    mocked_messages = [
        {
            "role": "system",
            "content": "You are an agent. " * 50
            + "TOOLS:\n"
            + json.dumps(_OPENAI_TOOL) * 3,
        },
        {"role": "user", "content": "do the task"},
    ]
    payload = _chat_log("mock-1", timestamp=1.0, tools=[_TOOL_DEFINITION_DUMP])
    payload["messages"] = mocked_messages
    payload["raw_messages"] = raw_messages
    _write_log(run, "gpt-4o-001.000-ab12.json", payload)

    report = build_report(run)

    assert report["skipped"] == []
    (row,) = report["rows"]
    assert row["tool_schema_counted"] is True
    assert row["composition"]["tool_schema_tokens"] == 0
    raw_composition = compute_prompt_composition(model="gpt-4o", messages=raw_messages)
    assert raw_composition is not None
    assert (
        row["composition"]["system_prompt_tokens"]
        > raw_composition.system_prompt_tokens
    )


def test_report_marks_uncountable_tool_schemas(tmp_path):
    """Logs serialize tools as ToolDefinition dumps (no parameter schemas), so
    the tool bucket is marked uncountable rather than silently wrong."""
    run = tmp_path / "run1"
    run.mkdir()
    payload = _chat_log("r1", timestamp=1.0, tools=[_TOOL_DEFINITION_DUMP])
    _write_log(run, "gpt-4o-001.000-ab12.json", payload)

    report = build_report(run)

    assert report["skipped"] == []
    (row,) = report["rows"]
    assert row["tool_schema_counted"] is False
    assert row["composition"]["tool_schema_tokens"] == 0
    assert row["composition"]["system_prompt_tokens"] > 0
    summary = report["summary"]
    assert summary["calls_with_uncountable_tool_schemas"] == 1
    # A row missing a bucket is excluded from the est/provider ratio.
    assert summary["est_provider_ratio_calls"] == 0
    assert summary["est_provider_median_ratio"] is None


def test_report_skips_garbage_files(tmp_path):
    run = tmp_path / "run1"
    run.mkdir()
    (run / "broken.json").write_text("{not json")
    _write_log(run, "not-a-log.json", {"hello": "world"})
    _write_log(run, "error-log.json", {**_chat_log("e1", 1.0), "error": {}})

    report = build_report(run)

    assert report["rows"] == []
    assert len(report["skipped"]) == 3
    assert report["summary"]["calls"] == 0


def test_write_report_emits_calls_jsonl_and_summary(tmp_path):
    run = tmp_path / "run1"
    run.mkdir()
    _write_log(run, "gpt-4o-001.000-ab12.json", _chat_log("r1", timestamp=1.0))
    report = build_report(run)

    calls_path, summary_path = write_report(report, tmp_path / "out")

    (row,) = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert row["seq"] == 0
    assert set(row) >= {"seq", "usage", "composition", "latency_s"}
    summary = json.loads(summary_path.read_text())
    assert summary["calls"] == 1
