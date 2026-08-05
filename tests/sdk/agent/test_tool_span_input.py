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
from openhands.sdk.conversation import Conversation
from openhands.sdk.llm import LLM, Message, TextContent
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
    """Real Laminar tracer writing to an in-memory exporter, torn down after.

    The exporter is installed *before* ``initialize`` so no OTLP endpoint is ever
    created; an unreachable one leaves every later test in the process retrying
    exports with backoff.
    """
    from lmnr import Laminar
    from lmnr.opentelemetry_lib.opentelemetry.instrumentation.threading import (
        ThreadingInstrumentor,
    )
    from lmnr.opentelemetry_lib.tracing import TracerWrapper

    if TracerWrapper.verify_initialized():
        pytest.skip("lmnr already initialized by another test in this process")

    exporter = InMemorySpanExporter()
    original_thread_init = threading.Thread.__init__
    TracerWrapper(
        exporter=exporter,
        disable_batch=True,
        instruments=set(),
        set_global_tracer_provider=False,
    )
    Laminar.initialize(
        project_api_key="test-key",
        disable_batch=True,
        instruments=set(),
        set_global_tracer_provider=False,
    )
    try:
        yield exporter.get_finished_spans
    finally:
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
