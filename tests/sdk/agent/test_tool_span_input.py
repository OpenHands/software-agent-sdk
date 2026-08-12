"""The TOOL span's input is the action, not the conversation object.

`tool(action, conversation)` would otherwise serialize the second argument as a
bare ``<LocalConversation object at 0x…>`` repr — no analytical value, a leaked
memory address, and dead weight through every downstream stage that scans it.
"""

import json
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import patch

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.agent.parallel_executor import ParallelToolExecutor
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.cancellation import CancellationToken
from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class _SpanInputAction(Action):
    value: str = ""


class _SpanInputObservation(Observation):
    result: str = ""


class _SpanInputExecutor(ToolExecutor[_SpanInputAction, _SpanInputObservation]):
    def __call__(
        self, action: _SpanInputAction, conversation=None
    ) -> _SpanInputObservation:
        return _SpanInputObservation(result=action.value)


class _SpanInputTool(ToolDefinition[_SpanInputAction, _SpanInputObservation]):
    name = "span_input_echo_tool"

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="Echoes its input",
                action_type=_SpanInputAction,
                observation_type=_SpanInputObservation,
                executor=_SpanInputExecutor(),
            )
        ]


register_tool("SpanInputEchoTool", _SpanInputTool)


@pytest.fixture
def exported():
    """Capture the spans this test emits, whatever the ambient lmnr state.

    Two paths, because this test must never skip — a skipped tracing test is
    indistinguishable from a passing one, and ``LMNR_*`` env vars are set in real
    CI. When lmnr is already up its span processor is borrowed and restored,
    which also keeps test spans off whatever real endpoint it was configured
    with. Otherwise one is built here, with the in-memory exporter installed
    *before* ``initialize`` so no OTLP endpoint is created — an unreachable one
    leaves later tests retrying exports with backoff.
    """
    from lmnr import Laminar
    from lmnr.opentelemetry_lib.opentelemetry.instrumentation.threading import (
        ThreadingInstrumentor,
    )
    from lmnr.opentelemetry_lib.tracing import TracerWrapper
    from lmnr.opentelemetry_lib.tracing.processor import LaminarSpanProcessor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    exporter = InMemorySpanExporter()
    borrowed = TracerWrapper.verify_initialized()
    original_thread_init = threading.Thread.__init__

    if not borrowed:
        TracerWrapper(
            exporter=exporter,
            disable_batch=True,
            instruments=set(),
            set_global_tracer_provider=False,
        )
    if not Laminar.is_initialized():
        # Respects an existing TracerWrapper rather than building a second one.
        Laminar.initialize(
            project_api_key="test-key",
            disable_batch=True,
            instruments=set(),
            set_global_tracer_provider=False,
        )

    processor = TracerWrapper.instance._span_processor
    assert isinstance(processor, LaminarSpanProcessor)
    previous = processor.instance
    processor.instance = SimpleSpanProcessor(exporter)
    try:
        yield exporter.get_finished_spans
    finally:
        processor.instance = previous
        if not borrowed:
            Laminar.shutdown()
            ThreadingInstrumentor().uninstrument()
            threading.Thread.__init__ = original_thread_init  # type: ignore[method-assign]
            TracerWrapper._original_thread_init = None
            if hasattr(TracerWrapper, "instance"):
                del TracerWrapper.instance


def _responses() -> Any:
    calls = {"n": 0}

    def fake(**kwargs: Any):
        calls["n"] += 1
        if calls["n"] == 1:
            message = LiteLLMMessage(
                role="assistant",
                content="checking",
                tool_calls=[
                    ChatCompletionMessageToolCall(
                        id="call_x",
                        type="function",
                        function=Function(
                            name="span_input_echo_tool",
                            arguments=json.dumps({"value": "hi"}),
                        ),
                    )
                ],
            )
            finish = "tool_calls"
        else:
            message = LiteLLMMessage(role="assistant", content="done", tool_calls=None)
            finish = "stop"
        return ModelResponse(
            id=f"r{calls['n']}",
            created=0,
            model="gpt-4o",
            object="chat.completion",
            choices=[Choices(index=0, message=message, finish_reason=finish)],
        )

    return fake


def _mixed_result_response(**kwargs: Any) -> ModelResponse:
    message = LiteLLMMessage(
        role="assistant",
        content="checking",
        tool_calls=[
            ChatCompletionMessageToolCall(
                id="call_valid",
                type="function",
                function=Function(
                    name="span_input_echo_tool",
                    arguments=json.dumps({"value": "hi"}),
                ),
            ),
            ChatCompletionMessageToolCall(
                id="call_invalid",
                type="function",
                function=Function(
                    name="span_input_echo_tool",
                    arguments=json.dumps({"bogus": True}),
                ),
            ),
            ChatCompletionMessageToolCall(
                id="call_missing",
                type="function",
                function=Function(name="missing_tool", arguments="{}"),
            ),
            ChatCompletionMessageToolCall(
                id="call_blocked",
                type="function",
                function=Function(
                    name="span_input_echo_tool",
                    arguments=json.dumps({"value": "blocked"}),
                ),
            ),
        ],
    )
    return ModelResponse(
        id="mixed-results",
        created=0,
        model="gpt-4o",
        object="chat.completion",
        choices=[Choices(index=0, message=message, finish_reason="tool_calls")],
    )


def test_tool_span_input_is_the_action_only(exported):
    llm = LLM(usage_id="probe", model="gpt-4o", api_key=SecretStr("k"))
    conversation = Conversation(
        agent=Agent(llm=llm, tools=[Tool(name="SpanInputEchoTool")]),
        callbacks=[],
    )
    with patch("openhands.sdk.llm.llm.litellm_completion", side_effect=_responses()):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="hi")])
        )
        conversation.run()
    conversation.close()

    tool_spans = [
        s for s in exported() if (s.attributes or {}).get("lmnr.span.type") == "TOOL"
    ]
    assert len(tool_spans) == 1
    payload = json.loads((tool_spans[0].attributes or {})["lmnr.span.input"])

    assert "conversation" not in payload
    assert payload["action"]["value"] == "hi"


def test_every_declared_tool_call_emits_one_result_span(exported):
    llm = LLM(usage_id="probe", model="gpt-4o", api_key=SecretStr("k"))
    agent = Agent(
        llm=llm,
        tools=[Tool(name="SpanInputEchoTool")],
        tool_concurrency_limit=2,
    )
    conversation = Conversation(agent=agent, callbacks=[])

    def on_event(event: Any) -> None:
        if isinstance(event, ActionEvent) and event.tool_call_id == "call_blocked":
            conversation.state.block_action(event.id, "blocked by policy")

    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=_mixed_result_response,
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="hi")])
        )
        agent.step(conversation, on_event=on_event)
    conversation.close()

    tool_spans = [
        span
        for span in exported()
        if (span.attributes or {}).get("lmnr.span.type") == "TOOL"
    ]
    spans_by_call = {
        (span.attributes or {})[
            "lmnr.association.properties.metadata.tool_call_id"
        ]: span
        for span in tool_spans
    }

    assert set(spans_by_call) == {
        "call_valid",
        "call_invalid",
        "call_missing",
        "call_blocked",
    }
    assert len(tool_spans) == len(spans_by_call)

    invalid_output = (spans_by_call["call_invalid"].attributes or {})[
        "lmnr.span.output"
    ]
    missing_output = (spans_by_call["call_missing"].attributes or {})[
        "lmnr.span.output"
    ]
    blocked_output = (spans_by_call["call_blocked"].attributes or {})[
        "lmnr.span.output"
    ]
    assert "Error validating tool" in invalid_output
    assert "Tool 'missing_tool' not found" in missing_output
    assert "Action rejected: blocked by policy" in blocked_output


def test_rejected_pending_tool_call_emits_one_result_span(exported):
    llm = LLM(usage_id="probe", model="gpt-4o", api_key=SecretStr("k"))
    agent = Agent(llm=llm, tools=[Tool(name="SpanInputEchoTool")])
    conversation = Conversation(agent=agent, callbacks=[])
    conversation.set_confirmation_policy(AlwaysConfirm())

    with patch("openhands.sdk.llm.llm.litellm_completion", side_effect=_responses()):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="hi")])
        )
        agent.step(conversation, on_event=conversation._on_event)
    conversation.reject_pending_actions("not approved")
    conversation.close()

    tool_spans = [
        span
        for span in exported()
        if (span.attributes or {}).get("lmnr.span.type") == "TOOL"
    ]
    root_spans = [span for span in exported() if span.name == "conversation"]
    assert len(tool_spans) == 1
    assert len(root_spans) == 1
    attributes = tool_spans[0].attributes or {}
    assert attributes["lmnr.association.properties.metadata.tool_call_id"] == "call_x"
    assert "Action rejected: not approved" in attributes["lmnr.span.output"]
    assert tool_spans[0].context is not None
    assert root_spans[0].context is not None
    assert tool_spans[0].context.trace_id == root_spans[0].context.trace_id


def test_cancelled_tool_call_span_shares_conversation_trace(exported):
    llm = LLM(usage_id="probe", model="gpt-4o", api_key=SecretStr("k"))
    conversation = Conversation(agent=Agent(llm=llm), callbacks=[])
    action = ActionEvent(
        thought=[TextContent(text="test")],
        tool_call=MessageToolCall(
            id="call_cancelled",
            name="span_input_echo_tool",
            arguments=json.dumps({"value": "hi"}),
            origin="completion",
        ),
        tool_name="span_input_echo_tool",
        tool_call_id="call_cancelled",
        llm_response_id="response",
    )
    token = CancellationToken()
    token.cancel()

    ParallelToolExecutor().execute_batch(
        [action],
        lambda _: pytest.fail("cancelled tool executed"),
        cancel_token=token,
        span_owner=conversation,
    )
    conversation.close()

    tool_spans = [
        span
        for span in exported()
        if (span.attributes or {}).get("lmnr.span.type") == "TOOL"
    ]
    root_spans = [span for span in exported() if span.name == "conversation"]
    assert len(tool_spans) == 1
    assert len(root_spans) == 1
    attributes = tool_spans[0].attributes or {}
    assert (
        attributes["lmnr.association.properties.metadata.tool_call_id"]
        == "call_cancelled"
    )
    assert "cancelled by interrupt" in attributes["lmnr.span.output"]
    assert tool_spans[0].context is not None
    assert root_spans[0].context is not None
    assert tool_spans[0].context.trace_id == root_spans[0].context.trace_id
