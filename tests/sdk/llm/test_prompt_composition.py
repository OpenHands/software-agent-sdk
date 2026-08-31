"""Tests for per-call prompt token composition estimates."""

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
