"""Tests for per-call prompt token composition estimates."""

from collections.abc import Sequence
from typing import ClassVar
from unittest.mock import AsyncMock, patch

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
from openhands.sdk.llm.utils.prompt_composition import compute_prompt_composition
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
