"""Regression tests for Codex subscription mode fixes.

Tests cover four bugs that made LLM.subscription_login() unusable:
1. prompt_cache_retention rejected by Codex endpoint (400)
2. include/reasoning params cause silent empty output
3. Streaming output items lost (response.completed has output=[])
4. Reasoning item IDs cause 404 on follow-up requests (store=false)

See: https://github.com/OpenHands/software-agent-sdk/issues/2797
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from litellm.types.llms.base import BaseLiteLLMOpenAIResponseObject
from openai.types.responses.response_function_tool_call import (
    ResponseFunctionToolCall,
)

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.message import (
    Message,
    MessageToolCall,
    ReasoningItemModel,
    TextContent,
)
from openhands.sdk.llm.options.responses_options import select_responses_options


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subscription_llm() -> LLM:
    """Create a minimal subscription-mode LLM for testing."""
    llm = LLM(
        model="openai/gpt-5.2-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        reasoning_effort="high",
    )
    llm._is_subscription = True
    llm.enable_encrypted_reasoning = True
    return llm


def _make_generic_output_item(**kwargs: Any) -> BaseLiteLLMOpenAIResponseObject:
    """Build a BaseLiteLLMOpenAIResponseObject (the type litellm uses for
    streaming output items) with the given attributes."""
    return BaseLiteLLMOpenAIResponseObject.model_construct(**kwargs)


# ---------------------------------------------------------------------------
# Bug 1 & 2: Unsupported params must be skipped in subscription mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "param",
    [
        "prompt_cache_retention",
        "include",
        "reasoning",
        "temperature",
        "max_output_tokens",
    ],
)
def test_subscription_skips_unsupported_param(param: str):
    """The Codex subscription endpoint rejects or silently mishandles these
    parameters.  They must be omitted when is_subscription is True."""
    llm = _make_subscription_llm()
    llm.max_output_tokens = 4096
    opts = select_responses_options(llm, {}, include=["text.output_text"], store=None)
    assert param not in opts


@pytest.mark.parametrize(
    "param,expected_value",
    [
        ("prompt_cache_retention", "24h"),
        ("temperature", 1.0),
    ],
)
def test_non_subscription_keeps_scalar_param(param: str, expected_value: Any):
    """Non-subscription GPT-5 models should still send these params."""
    llm = LLM(model="openai/gpt-5.2-codex", reasoning_effort="high")
    llm.enable_encrypted_reasoning = True
    assert not llm.is_subscription
    opts = select_responses_options(llm, {}, include=None, store=None)
    assert opts.get(param) == expected_value


@pytest.mark.parametrize(
    "param,check",
    [
        ("include", lambda v: "reasoning.encrypted_content" in v),
        ("reasoning", lambda v: v["effort"] == "high"),
    ],
)
def test_non_subscription_keeps_structured_param(param: str, check: Any):
    """Non-subscription LLMs should send include and reasoning normally."""
    llm = LLM(model="openai/gpt-5.2-codex", reasoning_effort="high")
    llm.enable_encrypted_reasoning = True
    assert not llm.is_subscription
    opts = select_responses_options(llm, {}, include=["text.output_text"], store=None)
    assert param in opts
    assert check(opts[param])


# ---------------------------------------------------------------------------
# Bug 3: from_llm_responses_output must handle generic litellm types
# ---------------------------------------------------------------------------


def _generic_function_call_item() -> BaseLiteLLMOpenAIResponseObject:
    return _make_generic_output_item(
        id="fc_abc",
        type="function_call",
        name="terminal",
        arguments='{"command": "ls"}',
        call_id="call_123",
        status="completed",
    )


def _generic_message_item() -> BaseLiteLLMOpenAIResponseObject:
    text_part = SimpleNamespace(type="output_text", text="Hello world")
    return _make_generic_output_item(
        id="m_1",
        type="message",
        role="assistant",
        status="completed",
        content=[text_part],
    )


def _generic_reasoning_item() -> BaseLiteLLMOpenAIResponseObject:
    summary = SimpleNamespace(type="summary_text", text="thinking")
    return _make_generic_output_item(
        id="rs_abc",
        type="reasoning",
        summary=[summary],
        content=None,
        encrypted_content=None,
        status="completed",
    )


def _dict_function_call_item() -> dict[str, Any]:
    return {
        "type": "function_call",
        "name": "file_editor",
        "arguments": '{"command": "view"}',
        "call_id": "call_456",
        "id": "fc_456",
    }


def _dict_message_item() -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Hi"}],
    }


def _typed_function_call_item() -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        type="function_call",
        name="think",
        arguments="{}",
        call_id="fc_typed",
        id="fc_typed",
    )


@pytest.mark.parametrize(
    "item_factory,expected_tool,expected_text",
    [
        pytest.param(
            _generic_function_call_item,
            {"name": "terminal", "arguments": '{"command": "ls"}', "id": "call_123"},
            None,
            id="generic-function-call",
        ),
        pytest.param(
            _dict_function_call_item,
            {
                "name": "file_editor",
                "arguments": '{"command": "view"}',
                "id": "call_456",
            },
            None,
            id="dict-function-call",
        ),
        pytest.param(
            _typed_function_call_item,
            {"name": "think", "arguments": "{}", "id": "fc_typed"},
            None,
            id="typed-function-call",
        ),
        pytest.param(
            _generic_message_item,
            None,
            "Hello world",
            id="generic-message",
        ),
        pytest.param(
            _dict_message_item,
            None,
            "Hi",
            id="dict-message",
        ),
    ],
)
def test_from_llm_responses_output_item_type(
    item_factory: Any,
    expected_tool: dict[str, str] | None,
    expected_text: str | None,
):
    """from_llm_responses_output must parse function_call and message items
    regardless of whether they arrive as typed Pydantic objects, generic
    BaseLiteLLMOpenAIResponseObject, or plain dicts."""
    item = item_factory()
    msg = Message.from_llm_responses_output([item])

    if expected_tool is not None:
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        tc = msg.tool_calls[0]
        assert tc.name == expected_tool["name"]
        assert tc.arguments == expected_tool["arguments"]
        assert tc.id == expected_tool["id"]
    if expected_text is not None:
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextContent)
        assert msg.content[0].text == expected_text


@pytest.mark.parametrize(
    "item_factory,expected_id,expected_summary",
    [
        pytest.param(
            _generic_reasoning_item,
            "rs_abc",
            ["thinking"],
            id="generic-reasoning",
        ),
    ],
)
def test_from_llm_responses_output_reasoning_item(
    item_factory: Any,
    expected_id: str,
    expected_summary: list[str],
):
    """Reasoning items from streaming should be parsed into ReasoningItemModel."""
    item = item_factory()
    msg = Message.from_llm_responses_output([item])
    assert msg.responses_reasoning_item is not None
    assert msg.responses_reasoning_item.id == expected_id
    assert msg.responses_reasoning_item.summary == expected_summary


def test_mixed_typed_and_generic_items():
    """Parser should handle a mix of typed and generic items in one call."""
    typed_fc = _typed_function_call_item()
    generic_fc = _generic_function_call_item()
    msg = Message.from_llm_responses_output([typed_fc, generic_fc])
    assert msg.tool_calls is not None
    assert len(msg.tool_calls) == 2
    assert {tc.name for tc in msg.tool_calls} == {"think", "terminal"}


# ---------------------------------------------------------------------------
# Bug 4: Reasoning item IDs must be stripped in subscription mode
# ---------------------------------------------------------------------------


def _make_conversation_messages() -> tuple[Message, Message, Message, Message]:
    """Build a minimal multi-turn conversation with a reasoning item."""
    sys_msg = Message(
        role="system",
        content=[TextContent(text="You are a helpful assistant.")],
    )
    user_msg = Message(
        role="user",
        content=[TextContent(text="Now create FACTS.txt")],
    )
    assistant_msg = Message(
        role="assistant",
        content=[TextContent(text="I'll look at the files.")],
        tool_calls=[
            MessageToolCall(
                id="call_1",
                name="terminal",
                arguments='{"command": "ls"}',
                origin="responses",
            )
        ],
        responses_reasoning_item=ReasoningItemModel(
            id="rs_should_be_stripped",
            summary=["thinking about files"],
            content=None,
            encrypted_content=None,
            status="completed",
        ),
    )
    tool_msg = Message(
        role="tool",
        content=[TextContent(text="file1.py file2.py")],
        tool_call_id="call_1",
    )
    return sys_msg, user_msg, assistant_msg, tool_msg


@pytest.mark.parametrize(
    "is_subscription,reasoning_id_present",
    [
        pytest.param(True, False, id="subscription-strips-reasoning"),
        pytest.param(False, True, id="non-subscription-preserves-reasoning"),
    ],
)
def test_format_messages_reasoning_item_handling(
    is_subscription: bool, reasoning_id_present: bool
):
    """Subscription mode must strip reasoning item IDs (store=false means they
    can't be resolved).  Non-subscription mode must preserve them."""
    llm = LLM(model="openai/gpt-5.2-codex")
    if is_subscription:
        llm._is_subscription = True

    sys_msg, user_msg, assistant_msg, tool_msg = _make_conversation_messages()
    _, input_items = llm.format_messages_for_responses(
        [sys_msg, user_msg, assistant_msg, tool_msg]
    )

    serialized = json.dumps(input_items, default=str)
    assert ("rs_should_be_stripped" in serialized) == reasoning_id_present
