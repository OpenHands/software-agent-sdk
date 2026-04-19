"""Tests that malformed tool call arguments are sanitized before entering the
event stream, preventing infinite retry loops with strict inference servers
like llama-server (see #2887).
"""

import json
from unittest.mock import patch

from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.agent.agent import _ensure_valid_tool_call_arguments
from openhands.sdk.conversation import Conversation
from openhands.sdk.event.llm_convertible import ActionEvent, AgentErrorEvent
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent


def test_ensure_valid_tool_call_arguments_valid():
    """Valid JSON arguments are returned unchanged."""
    tc = MessageToolCall(
        id="call_1",
        name="terminal",
        arguments='{"command": "ls"}',
        origin="completion",
    )
    result = _ensure_valid_tool_call_arguments(tc)
    assert result is tc  # same object — no copy needed


def test_ensure_valid_tool_call_arguments_malformed():
    """Malformed JSON arguments are replaced with '{}'."""
    tc = MessageToolCall(
        id="call_2",
        name="file_editor",
        arguments='{"command":"create","file_text":"unterminated',
        origin="completion",
    )
    result = _ensure_valid_tool_call_arguments(tc)
    assert result is not tc
    assert result.arguments == "{}"
    # Other fields are preserved
    assert result.id == "call_2"
    assert result.name == "file_editor"
    assert result.origin == "completion"


def test_malformed_tool_call_args_produce_valid_json_in_history():
    """End-to-end: malformed LLM tool call args -> valid JSON in events.

    Regression test for #2887: when an LLM returns tool call arguments
    that are not valid JSON, the agent must sanitize them before storing
    the ActionEvent. Otherwise inference servers like llama-server reject
    the prompt on the next turn.
    """
    malformed_args = '{"command":"create","file_text":"unterminated string'

    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[])

    def mock_llm_response(messages, **kwargs):
        return ModelResponse(
            id="mock-response-1",
            choices=[
                Choices(
                    index=0,
                    message=LiteLLMMessage(
                        role="assistant",
                        content="Creating file",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_bad",
                                type="function",
                                function=Function(
                                    name="file_editor",
                                    arguments=malformed_args,
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            created=0,
            model="test-model",
            object="chat.completion",
        )

    collected: list = []

    def cb(e):
        collected.append(e)

    conv = Conversation(agent=agent, callbacks=[cb])

    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=mock_llm_response,
    ):
        conv.send_message(
            Message(role="user", content=[TextContent(text="create a file")])
        )
        agent.step(conv, on_event=cb)

    # Find the ActionEvent with action=None (the error case)
    action_events = [
        e for e in collected if isinstance(e, ActionEvent) and e.action is None
    ]
    assert len(action_events) == 1

    action_event = action_events[0]
    # The stored tool_call arguments MUST be valid JSON
    stored_args = action_event.tool_call.arguments
    parsed = json.loads(stored_args)
    assert isinstance(parsed, dict)

    # The AgentErrorEvent carries the actual error details
    error_events = [e for e in collected if isinstance(e, AgentErrorEvent)]
    assert len(error_events) == 1

    # Verify the serialized message also has valid JSON
    msg = action_event.to_llm_message()
    assert msg.tool_calls is not None
    chat_dict = msg.to_chat_dict(
        cache_enabled=False,
        vision_enabled=False,
        function_calling_enabled=True,
        force_string_serializer=False,
        send_reasoning_content=False,
    )
    tc_args = chat_dict["tool_calls"][0]["function"]["arguments"]
    json.loads(tc_args)  # must not raise
