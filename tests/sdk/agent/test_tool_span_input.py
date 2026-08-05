"""The TOOL span's input is the action, not the conversation object.

`tool(action, conversation)` would otherwise serialize the second argument as a
bare ``<LocalConversation object at 0x…>`` repr — no analytical value, a leaked
memory address, and dead weight through every downstream stage that scans it.
"""

import json
import os
from typing import Any
from unittest.mock import patch

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.terminal import TerminalTool


register_tool("TerminalTool", TerminalTool)


@pytest.fixture
def exported():
    os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
    from lmnr import Instruments, Laminar
    from lmnr.opentelemetry_lib.tracing import TracerWrapper
    from lmnr.opentelemetry_lib.tracing.processor import LaminarSpanProcessor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    if not Laminar.is_initialized():
        Laminar.initialize(
            project_api_key="test-key",
            base_url="http://localhost",
            http_port=1,
            grpc_port=1,
            disable_batch=True,
            instruments={Instruments.LITELLM},
        )
    exporter = InMemorySpanExporter()
    processor = TracerWrapper.instance._span_processor
    assert isinstance(processor, LaminarSpanProcessor)
    previous = processor.instance
    processor.instance = SimpleSpanProcessor(exporter)
    try:
        yield lambda: exporter.get_finished_spans()
    finally:
        processor.instance = previous


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
                            name="execute_bash",
                            arguments=json.dumps({"command": "echo hi"}),
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


def test_tool_span_input_is_the_action_only(exported, tmp_path):
    llm = LLM(usage_id="probe", model="gpt-4o", api_key=SecretStr("k"))
    conversation = Conversation(
        agent=Agent(llm=llm, tools=[Tool(name="TerminalTool")]),
        callbacks=[],
        workspace=str(tmp_path),
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
    assert payload["action"]["command"] == "echo hi"
