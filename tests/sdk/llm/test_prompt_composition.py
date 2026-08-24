"""Tests for per-call prompt token composition estimates."""

from collections.abc import Sequence
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from litellm.types.llms.openai import ResponseAPIUsage, ResponsesAPIResponse
from litellm.types.utils import (
    Choices,
    Message as LiteLLMMessage,
    ModelResponse,
    Usage,
)
from openai.types.responses.response_output_message import ResponseOutputMessage
from openai.types.responses.response_output_text import ResponseOutputText
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.utils.metrics import Metrics, PromptComposition
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


def _chat_response(response_id: str = "resp-1") -> ModelResponse:
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content="ok", role="assistant"),
            )
        ],
        created=0,
        model="gpt-4o",
        object="chat.completion",
        usage=Usage(prompt_tokens=100, completion_tokens=5, total_tokens=105),
    )


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
    assert composition.tool_tokens > 0
    assert composition.history_tokens > 0
    assert composition.latest_message_tokens > 0
    # Buckets are counted independently with per-message framing overhead,
    # so their sum covers the message content of the whole prompt.
    from litellm.utils import token_counter

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
    assert composition.tool_tokens == 0
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


def test_compute_prompt_composition_tool_tokens_matches_controlled_delta():
    """tool_tokens must equal the marginal cost of adding tools to the call."""
    from litellm.utils import token_counter

    formatted = _make_llm().format_messages_for_llm(_sample_messages())
    tools = [
        t.to_openai_tool(add_security_risk_prediction=True) for t in _MockTool.create()
    ]

    with_tools = compute_prompt_composition(
        model="gpt-4o", messages=formatted, tools=tools
    )
    without_tools = compute_prompt_composition(model="gpt-4o", messages=formatted)
    assert with_tools is not None and without_tools is not None
    assert without_tools.tool_tokens == 0

    component_sum = (
        with_tools.system_prompt_tokens
        + with_tools.tool_tokens
        + with_tools.history_tokens
        + with_tools.latest_message_tokens
    )
    other_components = (
        without_tools.system_prompt_tokens
        + without_tools.history_tokens
        + without_tools.latest_message_tokens
    )
    assert component_sum - other_components == with_tools.tool_tokens

    # And the estimate tracks the real marginal cost of the tools closely.
    real_delta = token_counter(
        model="gpt-4o", messages=formatted, tools=tools
    ) - token_counter(model="gpt-4o", messages=formatted)
    assert abs(with_tools.tool_tokens - real_delta) <= 8


def test_completion_records_prompt_composition():
    llm = _make_llm()
    tools = list(_MockTool.create())

    with patch(
        "openhands.sdk.llm.llm.litellm_completion", return_value=_chat_response()
    ):
        llm.completion(messages=_sample_messages(), tools=tools)

    composition = llm.metrics.latest_prompt_composition
    assert composition is not None
    assert composition.response_id == "resp-1"
    # Agent-step style call: tool schemas are part of the prompt.
    assert composition.tool_tokens > 0
    assert composition.system_prompt_tokens > 0
    assert composition.history_tokens > 0
    assert composition.latest_message_tokens > 0


def test_completion_without_tools_records_zero_tool_tokens():
    llm = _make_llm()

    with patch(
        "openhands.sdk.llm.llm.litellm_completion", return_value=_chat_response()
    ):
        llm.completion(messages=_sample_messages())

    composition = llm.metrics.latest_prompt_composition
    assert composition is not None
    assert composition.tool_tokens == 0


async def test_acompletion_records_prompt_composition():
    llm = _make_llm()

    with patch(
        "openhands.sdk.llm.llm.litellm_acompletion",
        new_callable=AsyncMock,
        return_value=_chat_response(),
    ):
        await llm.acompletion(messages=_sample_messages())

    assert llm.metrics.latest_prompt_composition is not None


def test_responses_records_prompt_composition():
    llm = _make_llm("gpt-5-mini")
    output = ResponseOutputMessage.model_construct(
        id="m1",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text="ok", annotations=[])],
    )
    resp = ResponsesAPIResponse(
        id="r1",
        created_at=0,
        output=[output],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=ResponseAPIUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        status="completed",
    )

    with patch("openhands.sdk.llm.llm.litellm_responses", return_value=resp):
        llm.responses(_sample_messages(), tools=list(_MockTool.create()))

    composition = llm.metrics.latest_prompt_composition
    assert composition is not None
    assert composition.response_id == "r1"
    assert composition.system_prompt_tokens > 0
    assert composition.tool_tokens > 0
    assert composition.latest_message_tokens > 0


def test_metrics_merge_and_diff_include_prompt_compositions():
    baseline = Metrics(model_name="gpt-4o")
    baseline.add_prompt_composition(
        PromptComposition(system_prompt_tokens=10), response_id="r1"
    )
    current = baseline.deep_copy()
    current.add_prompt_composition(
        PromptComposition(system_prompt_tokens=20), response_id="r2"
    )

    diff = current.diff(baseline)
    assert len(diff.prompt_compositions) == 1
    assert diff.prompt_compositions[0].response_id == "r2"

    merged = Metrics(model_name="gpt-4o")
    merged.merge(current)
    assert len(merged.prompt_compositions) == 2
    assert merged.latest_prompt_composition is not None
    assert merged.latest_prompt_composition.response_id == "r2"


def test_metrics_loads_payload_without_prompt_compositions():
    metrics = Metrics.model_validate({"model_name": "gpt-4o", "accumulated_cost": 1.0})

    assert metrics.prompt_compositions == []
    assert metrics.latest_prompt_composition is None


def test_mock_tools_does_not_double_count_tool_schemas():
    """With prompt-mocked tools, schemas live in the prompt text: tool_tokens
    must be 0 (no double count) while the schemas still inflate the system
    bucket."""
    mock_response = ModelResponse(
        id="mock-resp",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(
                    content=(
                        "I'll help.\n"
                        "<function=test_tool>\n"
                        "<parameter=param>test_value</parameter>\n"
                        "</function>"
                    ),
                    role="assistant",
                ),
            )
        ],
        created=0,
        model="gpt-4o",
        object="chat.completion",
        usage=Usage(prompt_tokens=100, completion_tokens=5, total_tokens=105),
    )
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test"),
        usage_id="test-llm",
        native_tool_calling=False,
    )

    with patch("openhands.sdk.llm.llm.litellm_completion", return_value=mock_response):
        llm.completion(messages=_sample_messages(), tools=list(_MockTool.create()))
        with_tools = llm.metrics.latest_prompt_composition
        llm.completion(messages=_sample_messages())
        without_tools = llm.metrics.latest_prompt_composition

    assert with_tools is not None and without_tools is not None
    assert with_tools.tool_tokens == 0
    assert with_tools.system_prompt_tokens > without_tools.system_prompt_tokens


def _responses_api_response() -> ResponsesAPIResponse:
    output = ResponseOutputMessage.model_construct(
        id="m1",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text="ok", annotations=[])],
    )
    return ResponsesAPIResponse(
        id="r1",
        created_at=0,
        output=[output],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=ResponseAPIUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        status="completed",
    )


def test_responses_composition_survives_tool_serialization_failure():
    """A tool that fails chat-format serialization must skip the composition
    record, not break the real Responses call."""
    llm = _make_llm("gpt-5-mini")
    tool = list(_MockTool.create())[0]

    with (
        patch(
            "openhands.sdk.llm.llm.litellm_responses",
            return_value=_responses_api_response(),
        ),
        patch.object(
            type(tool),
            "to_openai_tool",
            side_effect=RuntimeError("cannot serialize"),
        ),
    ):
        response = llm.responses(_sample_messages(), tools=[tool])

    assert response.message.role == "assistant"
    assert llm.metrics.latest_prompt_composition is None


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
