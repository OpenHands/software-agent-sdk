"""Tests that the agent emits ActionEvent with action=None on missing tools."""

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
from openhands.sdk.conversation import Conversation
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
)
from openhands.sdk.llm import LLM, Message, TextContent


def test_emits_action_event_with_none_action_then_error_on_missing_tool() -> None:
    """Test that agent emits ActionEvent(action=None) when tool is missing."""
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
                        content="I'll use a non-existent tool to help you.",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_x",
                                type="function",
                                function=Function(
                                    name="nonexistent_tool",
                                    arguments=json.dumps({"param": "value"}),
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

    collected = []

    def cb(e):
        collected.append(e)

    conv = Conversation(agent=agent, callbacks=[cb])

    with patch(
        "openhands.sdk.llm.llm.litellm_acompletion", side_effect=mock_llm_response
    ):
        conv.send_message(Message(role="user", content=[TextContent(text="go")]))
        agent.step(conv, on_event=cb)

    # Find ActionEvent with action=None
    action_events_none = [
        e for e in collected if isinstance(e, ActionEvent) and e.action is None
    ]
    error_events = [e for e in collected if isinstance(e, AgentErrorEvent)]

    # We expect at least one ActionEvent with action=None and one AgentErrorEvent
    assert len(action_events_none) > 0
    assert len(error_events) > 0

    # Ensure ordering: ActionEvent(action=None) occurs before AgentErrorEvent
    first_action_none_idx = next(
        i
        for i, e in enumerate(collected)
        if isinstance(e, ActionEvent) and e.action is None
    )
    first_err_idx = next(
        i for i, e in enumerate(collected) if isinstance(e, AgentErrorEvent)
    )
    assert first_action_none_idx < first_err_idx

    # Verify tool_call_id continuity
    action_event = action_events_none[0]
    tc_id = action_event.tool_call.id
    err = error_events[0]
    assert err.tool_call_id == tc_id

    # Ensure message event exists for the initial system prompt
    assert any(isinstance(e, MessageEvent) for e in collected)
