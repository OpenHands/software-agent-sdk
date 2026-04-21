"""Tests for native-API adapters (``to_native`` / ``from_native``).

Each family (Anthropic / Gemini / Responses / OpenAI Chat) has a tiny adapter
pair. These tests lock in the wire-format contract — any change here is a
potential API break for the downstream agent loop that expects OpenAI Chat.

Live E2E already confirmed the adapters work against real endpoints (PAT →
llama, PROFILE → claude, UNIFIED → gemini). This file pins the wire shape.
"""

from __future__ import annotations

import pytest

from openhands.sdk.llm.providers.databricks.models import ProviderFamily
from openhands.sdk.llm.providers.databricks.native import (
    _chat_tools_to_responses,
    _flatten_content,
    from_native,
    to_native,
)


# ---------------------------------------------------------------------------
# _flatten_content — reasoning-model content blocks
# ---------------------------------------------------------------------------

def test_flatten_content_string_passthrough() -> None:
    assert _flatten_content("hello") == "hello"


def test_flatten_content_text_blocks_are_concatenated() -> None:
    blocks = [
        {"type": "text", "text": "Hello "},
        {"type": "text", "text": "world"},
    ]
    assert _flatten_content(blocks) == "Hello world"


def test_flatten_content_reasoning_blocks_are_dropped() -> None:
    """Reasoning blocks are model-internal thought and must never leak out."""
    blocks = [
        {"type": "reasoning", "text": "thinking hard..."},
        {"type": "text", "text": "answer"},
    ]
    assert _flatten_content(blocks) == "answer"


def test_flatten_content_garbage_returns_empty() -> None:
    assert _flatten_content(None) == ""
    assert _flatten_content(42) == ""
    assert _flatten_content([42, "x"]) == ""


# ---------------------------------------------------------------------------
# OpenAI Chat (default family)
# ---------------------------------------------------------------------------

def test_to_openai_chat_minimal_payload() -> None:
    """The mlflow path doesn't carry the endpoint in the URL — it must be in the body."""
    body = to_native(
        ProviderFamily.OPENAI,
        "databricks-llama",
        [{"role": "user", "content": "hi"}],
    )
    assert body == {
        "model": "databricks-llama",
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_to_openai_chat_forwards_generation_kwargs() -> None:
    body = to_native(
        ProviderFamily.OPENAI, "m",
        [{"role": "user", "content": "x"}],
        max_tokens=32, temperature=0.2, top_p=0.9, stop=["END"],
    )
    assert body["max_tokens"] == 32
    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.9
    assert body["stop"] == ["END"]


def test_to_openai_chat_includes_tools_and_tool_choice() -> None:
    tools = [{"type": "function", "function": {"name": "get_time"}}]
    body = to_native(
        ProviderFamily.OPENAI, "m",
        [{"role": "user", "content": "what time"}],
        tools=tools, tool_choice="auto",
    )
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"


def test_to_openai_chat_stream_flag_propagates() -> None:
    body = to_native(ProviderFamily.OPENAI, "m",
                     [{"role": "user", "content": "x"}], stream=True)
    assert body["stream"] is True


def test_from_openai_chat_passthrough() -> None:
    """OpenAI Chat is the native format — responses should pass through intact."""
    raw = {
        "id": "chatcmpl-1", "object": "chat.completion", "model": "m",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": "hi"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }
    out = from_native(ProviderFamily.OPENAI, "m", raw)
    assert out["id"] == "chatcmpl-1"
    assert out["choices"][0]["message"]["content"] == "hi"


def test_from_openai_chat_flattens_list_content_for_reasoning_models() -> None:
    """gpt-oss-style list-of-blocks content must be flattened to a string."""
    raw = {
        "choices": [{"message": {"content": [
            {"type": "reasoning", "text": "think"},
            {"type": "text", "text": "answer"},
        ]}}],
    }
    out = from_native(ProviderFamily.OPENAI, "m", raw)
    assert out["choices"][0]["message"]["content"] == "answer"


# ---------------------------------------------------------------------------
# Anthropic Messages
# ---------------------------------------------------------------------------

def test_to_anthropic_hoists_system_message() -> None:
    body = to_native(
        ProviderFamily.ANTHROPIC, "databricks-claude-sonnet-4-5",
        [
            {"role": "system", "content": "You are a poet."},
            {"role": "user",   "content": "Write a haiku."},
        ],
    )
    assert body["system"] == "You are a poet."
    assert body["messages"] == [{"role": "user", "content": "Write a haiku."}]
    assert "system" not in {m["role"] for m in body["messages"]}


def test_to_anthropic_requires_max_tokens_even_if_unspecified() -> None:
    """Anthropic Messages rejects requests without max_tokens — we default it."""
    body = to_native(
        ProviderFamily.ANTHROPIC, "m",
        [{"role": "user", "content": "hi"}],
    )
    assert "max_tokens" in body
    assert isinstance(body["max_tokens"], int) and body["max_tokens"] > 0


def test_to_anthropic_stop_maps_to_stop_sequences() -> None:
    body = to_native(
        ProviderFamily.ANTHROPIC, "m",
        [{"role": "user", "content": "x"}],
        stop="END",
    )
    assert body["stop_sequences"] == ["END"]
    body = to_native(
        ProviderFamily.ANTHROPIC, "m",
        [{"role": "user", "content": "x"}],
        stop=["A", "B"],
    )
    assert body["stop_sequences"] == ["A", "B"]


def test_to_anthropic_includes_model_id() -> None:
    """Anthropic path is endpoint-agnostic — model id travels in the body."""
    body = to_native(
        ProviderFamily.ANTHROPIC, "databricks-claude-opus-4-6",
        [{"role": "user", "content": "x"}],
    )
    assert body["model"] == "databricks-claude-opus-4-6"


def test_from_anthropic_extracts_text_blocks() -> None:
    raw = {
        "id": "msg_abc", "model": "claude-sonnet",
        "content": [{"type": "text", "text": "Hello there."}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 3},
    }
    out = from_native(ProviderFamily.ANTHROPIC, "claude-sonnet", raw)
    assert out["id"] == "msg_abc"
    assert out["choices"][0]["message"]["content"] == "Hello there."
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"]["prompt_tokens"] == 10
    assert out["usage"]["completion_tokens"] == 3
    assert out["usage"]["total_tokens"] == 13


@pytest.mark.parametrize(
    "stop_reason,expected",
    [("end_turn", "stop"), ("stop_sequence", "stop"),
     ("max_tokens", "length"), ("tool_use", "tool_calls")],
)
def test_from_anthropic_stop_reason_mapping(stop_reason: str, expected: str) -> None:
    raw = {"content": [{"type": "text", "text": "x"}],
           "stop_reason": stop_reason, "usage": {}}
    out = from_native(ProviderFamily.ANTHROPIC, "m", raw)
    assert out["choices"][0]["finish_reason"] == expected


# ---------------------------------------------------------------------------
# Google Gemini generateContent
# ---------------------------------------------------------------------------

def test_to_gemini_builds_contents_and_system_instruction() -> None:
    body = to_native(
        ProviderFamily.GEMINI, "databricks-gemini-2-5-flash",
        [
            {"role": "system",    "content": "Be concise."},
            {"role": "user",      "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
        ],
    )
    assert body["systemInstruction"] == {"parts": [{"text": "Be concise."}]}
    assert body["contents"] == [
        {"role": "user",  "parts": [{"text": "What is 2+2?"}]},
        {"role": "model", "parts": [{"text": "4"}]},   # assistant → model
    ]


def test_to_gemini_sets_max_output_tokens_with_safe_default() -> None:
    """Gemini spends budget on thinking — default must be large enough for output."""
    body = to_native(
        ProviderFamily.GEMINI, "m",
        [{"role": "user", "content": "x"}],
    )
    assert body["generationConfig"]["maxOutputTokens"] >= 256


def test_to_gemini_maps_stop_to_stop_sequences() -> None:
    body = to_native(
        ProviderFamily.GEMINI, "m",
        [{"role": "user", "content": "x"}],
        stop=["END"],
    )
    assert body["generationConfig"]["stopSequences"] == ["END"]


def test_from_gemini_extracts_text_and_usage() -> None:
    raw = {
        "candidates": [{
            "content": {"role": "model", "parts": [
                {"text": "Four."},
            ]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {
            "promptTokenCount": 5, "candidatesTokenCount": 2, "totalTokenCount": 7,
        },
        "responseId": "gen-abc",
    }
    out = from_native(ProviderFamily.GEMINI, "m", raw)
    assert out["choices"][0]["message"]["content"] == "Four."
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"] == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
    assert out["id"] == "gen-abc"


@pytest.mark.parametrize(
    "finish,expected",
    [("STOP", "stop"), ("MAX_TOKENS", "length"),
     ("SAFETY", "content_filter"), ("RECITATION", "content_filter"),
     ("UNKNOWN_REASON", "stop")],
)
def test_from_gemini_finish_reason_mapping(finish: str, expected: str) -> None:
    raw = {"candidates": [{"content": {"parts": [{"text": "x"}]},
                           "finishReason": finish}]}
    out = from_native(ProviderFamily.GEMINI, "m", raw)
    assert out["choices"][0]["finish_reason"] == expected


# ---------------------------------------------------------------------------
# OpenAI Responses (GPT-5 series)
# ---------------------------------------------------------------------------

def test_to_responses_uses_input_not_messages() -> None:
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES, "databricks-gpt-5-4",
        [{"role": "user", "content": "say hi"}],
    )
    assert "messages" not in body
    # Responses requires content parts of type ``input_text`` for user
    # messages — string content is wrapped accordingly.
    assert body["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "say hi"}]}
    ]
    assert body["model"] == "databricks-gpt-5-4"


def test_to_responses_renames_max_tokens_to_max_output_tokens() -> None:
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES, "m",
        [{"role": "user", "content": "x"}],
        max_tokens=256,
    )
    assert "max_tokens" not in body
    assert body["max_output_tokens"] == 256


def test_to_responses_default_budget_accommodates_reasoning_tokens() -> None:
    """GPT-5 spends tokens on reasoning — default must not be too small."""
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES, "m",
        [{"role": "user", "content": "x"}],
    )
    assert body["max_output_tokens"] >= 512


def test_to_responses_drops_gateway_unsupported_kwargs() -> None:
    """Gateway rejects ``background`` / ``store`` / ``previous_response_id`` etc."""
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES, "m",
        [{"role": "user", "content": "x"}],
        background=True, store=True,
        previous_response_id="resp_xyz", service_tier="flex",
    )
    for dropped in ("background", "store", "previous_response_id", "service_tier"):
        assert dropped not in body, f"{dropped!r} must be dropped — gateway rejects it"


def test_to_responses_drops_temperature_and_top_p() -> None:
    """GPT-5 reasoning models reject ``temperature`` and ``top_p``.

    The default ``LLM`` ships ``temperature=0.0`` for everyone, so silently
    dropping them in the Responses adapter is the only way single-default
    callers can talk to GPT-5 at all.
    """
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES, "m",
        [{"role": "user", "content": "x"}],
        temperature=0.0,
        top_p=0.5,
    )
    assert "temperature" not in body
    assert "top_p" not in body


def test_to_responses_translates_user_text_part_to_input_text() -> None:
    """Responses rejects content-part type ``"text"`` for user messages.

    Chat-Completions style ``[{"type": "text", ...}]`` must become
    ``[{"type": "input_text", ...}]`` for user/system roles, and string
    content must be wrapped in a single ``input_text`` part.
    """
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES, "m",
        [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "system", "content": "you are helpful"},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ],
    )
    assert body["input"][0]["content"][0] == {"type": "input_text", "text": "hi"}
    assert body["input"][1]["content"][0] == {
        "type": "input_text",
        "text": "you are helpful",
    }
    # Assistant text parts use output_text.
    assert body["input"][2]["content"][0] == {"type": "output_text", "text": "ok"}


def test_to_responses_drops_chat_style_max_completion_tokens_alias() -> None:
    """Upstream LLM/litellm path emits ``max_completion_tokens`` for OpenAI-flavoured
    calls; the Responses API rejects it (``unsupported_parameter`` 400). The
    adapter must drop the chat-style alias and only forward ``max_output_tokens``.
    """
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES, "m",
        [{"role": "user", "content": "x"}],
        max_tokens=128,
        max_completion_tokens=128,
    )
    assert "max_completion_tokens" not in body
    assert body["max_output_tokens"] == 128


def test_from_responses_flattens_message_items_and_skips_reasoning() -> None:
    """Responses output is an array of items; we extract text, skip reasoning."""
    raw = {
        "id": "resp_abc",
        "status": "completed",
        "output": [
            {"type": "reasoning", "content": [{"type": "text", "text": "thinking..."}]},
            {"type": "message", "content": [
                {"type": "output_text", "text": "The answer is 42."},
            ]},
        ],
        "usage": {"input_tokens": 4, "output_tokens": 8, "total_tokens": 12},
    }
    out = from_native(ProviderFamily.OPENAI_RESPONSES, "m", raw)
    assert out["id"] == "resp_abc"
    assert out["choices"][0]["message"]["content"] == "The answer is 42."
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"]["total_tokens"] == 12


def test_from_responses_converts_function_call_to_tool_calls() -> None:
    """Responses function_call items must be converted to Chat Completions tool_calls.

    Without this, GPT-5 tool invocations are silently dropped and OpenHands
    loops with 'response did not include a function call'.
    """
    raw = {
        "id": "resp_xyz",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "id": "fc_abc",
                "call_id": "call_abc",
                "name": "terminal",
                "arguments": '{"command": "ls"}',
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    out = from_native(ProviderFamily.OPENAI_RESPONSES, "gpt-5", raw)
    msg = out["choices"][0]["message"]
    assert "tool_calls" in msg
    assert len(msg["tool_calls"]) == 1
    tc = msg["tool_calls"][0]
    assert tc["id"] == "call_abc"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "terminal"
    assert tc["function"]["arguments"] == '{"command": "ls"}'
    assert out["choices"][0]["finish_reason"] == "tool_calls"


def test_from_responses_mixed_text_and_tool_call() -> None:
    """Text and function_call in same response are both captured."""
    raw = {
        "id": "resp_mix",
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Running..."}]},
            {"type": "function_call", "call_id": "call_1", "name": "read_file",
             "arguments": '{"path": "foo.py"}'},
        ],
        "usage": {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10},
    }
    out = from_native(ProviderFamily.OPENAI_RESPONSES, "gpt-5", raw)
    msg = out["choices"][0]["message"]
    assert msg["content"] == "Running..."
    assert len(msg["tool_calls"]) == 1
    assert msg["tool_calls"][0]["function"]["name"] == "read_file"


def test_from_responses_falls_back_to_aggregated_output_text() -> None:
    """When ``output`` is absent but the response exposes flat ``output_text``,
    consume that as the assistant message content."""
    raw = {"id": "resp_1", "status": "completed", "output_text": "agg text"}
    out = from_native(ProviderFamily.OPENAI_RESPONSES, "m", raw)
    assert out["choices"][0]["message"]["content"] == "agg text"


# ---------------------------------------------------------------------------
# _chat_tools_to_responses — tool format conversion
# ---------------------------------------------------------------------------

def test_chat_tools_to_responses_unwraps_function_wrapper() -> None:
    """Chat Completions tool {type, function: {name, ...}} → Responses {type, name, ...}."""
    chat_tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal",
                "description": "Run a shell command",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    result = _chat_tools_to_responses(chat_tools)
    assert len(result) == 1
    tool = result[0]
    # name must be at top level (Responses API requirement)
    assert tool["name"] == "terminal"
    assert tool["type"] == "function"
    assert tool["description"] == "Run a shell command"
    # function wrapper must be gone
    assert "function" not in tool


def test_chat_tools_to_responses_multiple_tools() -> None:
    """Conversion handles multiple tools correctly."""
    chat_tools = [
        {"type": "function", "function": {"name": "read_file", "description": "r", "parameters": {}}},
        {"type": "function", "function": {"name": "write_file", "description": "w", "parameters": {}}},
    ]
    result = _chat_tools_to_responses(chat_tools)
    assert [t["name"] for t in result] == ["read_file", "write_file"]
    assert all("function" not in t for t in result)


def test_to_responses_converts_tool_format() -> None:
    """to_native for OPENAI_RESPONSES flattens tool wrappers into Responses format."""
    chat_tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal",
                "description": "Run a shell command",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES,
        "databricks-gpt-5-4",
        [{"role": "user", "content": "hello"}],
        tools=chat_tools,
    )
    assert "tools" in body
    tool = body["tools"][0]
    assert tool["name"] == "terminal"
    assert tool["type"] == "function"
    assert "function" not in tool


# ---------------------------------------------------------------------------
# _to_responses_input — multi-turn tool call translation
# ---------------------------------------------------------------------------


def test_to_responses_tool_role_becomes_function_call_output() -> None:
    """``role=tool`` messages must become ``function_call_output`` items.

    When GPT-5 returns a function_call on turn 1, OpenHands sends back the
    result as a Chat Completions ``role=tool`` message on turn 2.  The Responses
    API requires this to be a ``function_call_output`` item (not a message with
    role) — otherwise the second turn fails with a schema error.
    """
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES,
        "databricks-gpt-5-4",
        [
            {"role": "user", "content": "Write hello.py"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": '{"path":"hello.py","content":"hi"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc",
                "content": "File written successfully.",
            },
        ],
    )
    items = body["input"]
    types = [i.get("type") or i.get("role") for i in items]
    # user message preserved
    assert types[0] == "user", f"expected user, got {items[0]}"
    # assistant tool_call → function_call item
    assert types[1] == "function_call", f"expected function_call, got {items[1]}"
    assert items[1]["call_id"] == "call_abc"
    assert items[1]["name"] == "write_file"
    # tool result → function_call_output item
    assert types[2] == "function_call_output", f"expected function_call_output, got {items[2]}"
    assert items[2]["call_id"] == "call_abc"
    assert items[2]["output"] == "File written successfully."


def test_to_responses_assistant_with_tool_calls_emits_function_call_items() -> None:
    """Assistant messages with ``tool_calls`` become ``function_call`` input items."""
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES,
        "m",
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": '{"path":"b.py","content":"x"}'},
                    },
                ],
            },
        ],
    )
    items = body["input"]
    # items[0] = user message, items[1] = function_call (read_file), items[2] = function_call (write_file)
    assert len(items) == 3
    assert items[1]["type"] == "function_call"
    assert items[1]["name"] == "read_file"
    assert items[1]["call_id"] == "call_1"
    assert items[2]["type"] == "function_call"
    assert items[2]["name"] == "write_file"
    assert items[2]["call_id"] == "call_2"


def test_to_responses_assistant_with_tool_calls_and_text_emits_both() -> None:
    """Assistant message with both text content and tool_calls emits both items."""
    body = to_native(
        ProviderFamily.OPENAI_RESPONSES,
        "m",
        [
            {
                "role": "assistant",
                "content": "I'll run that for you.",
                "tool_calls": [
                    {
                        "id": "call_x",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": '{"cmd":"ls"}'},
                    }
                ],
            },
        ],
    )
    items = body["input"]
    fc = next((i for i in items if i.get("type") == "function_call"), None)
    assert fc is not None, "function_call item missing"
    assert fc["name"] == "terminal"
    text_items = [i for i in items if i.get("role") == "assistant"]
    assert text_items, "output_text item for text content missing"
    assert text_items[0]["content"][0]["text"] == "I'll run that for you."
