"""Test agent behavior when tool execution raises ValueError."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self
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
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, AgentErrorEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class RaisingAction(Action):
    """Action that will cause the executor to raise ValueError."""

    value: str = ""


class RaisingObservation(Observation):
    """Observation for the raising tool."""

    result: str = ""


class RaisingExecutor(ToolExecutor[RaisingAction, RaisingObservation]):
    """Executor that raises ValueError."""

    def __call__(self, action: RaisingAction, conversation=None) -> RaisingObservation:
        raise ValueError("Cannot use reset=True with is_input=True")


class RaisingTool(ToolDefinition[RaisingAction, RaisingObservation]):
    """Tool that raises ValueError during execution."""

    name = "raising_tool"

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="A tool that raises ValueError",
                action_type=RaisingAction,
                observation_type=RaisingObservation,
                executor=RaisingExecutor(),
            )
        ]


# Register the tool so it can be resolved by name
register_tool("RaisingTool", RaisingTool)


def test_tool_execution_valueerror_returns_error_event():
    """Test that ValueError from tool execution returns AgentErrorEvent."""

    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[Tool(name="RaisingTool")])

    def mock_llm_response(messages, **kwargs):
        return ModelResponse(
            id="mock-response-1",
            choices=[
                Choices(
                    index=0,
                    message=LiteLLMMessage(
                        role="assistant",
                        content="I'll use the raising tool.",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_1",
                                type="function",
                                function=Function(
                                    name="raising_tool",
                                    arguments='{"value": "test"}',
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

    collected_events = []

    def event_callback(event):
        collected_events.append(event)

    conversation = Conversation(agent=agent, callbacks=[event_callback])

    with patch(
        "openhands.sdk.llm.llm.litellm_completion", side_effect=mock_llm_response
    ):
        conversation.send_message(
            Message(
                role="user",
                content=[TextContent(text="Please use the raising tool.")],
            )
        )

        # Run one step to trigger the tool call
        agent.step(conversation, on_event=event_callback)

    # Verify that an AgentErrorEvent was generated
    error_events = [e for e in collected_events if isinstance(e, AgentErrorEvent)]
    assert len(error_events) == 1, (
        f"Expected 1 AgentErrorEvent, got {len(error_events)}"
    )

    error_event = error_events[0]
    assert "raising_tool" in error_event.error
    assert "Cannot use reset=True with is_input=True" in error_event.error
    assert error_event.tool_name == "raising_tool"
    assert error_event.tool_call_id == "call_1"

    # Verify that the conversation is NOT finished
    with conversation.state:
        assert (
            conversation.state.execution_status != ConversationExecutionStatus.FINISHED
        ), "Agent should not be finished after tool execution error"


def test_conversation_continues_after_tool_execution_error():
    """Test that conversation can continue after a tool execution error."""

    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[Tool(name="RaisingTool")])

    call_count = 0

    def mock_llm_response(messages, **kwargs):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            # First call: try the raising tool
            return ModelResponse(
                id="mock-response-1",
                choices=[
                    Choices(
                        index=0,
                        message=LiteLLMMessage(
                            role="assistant",
                            content="I'll try the raising tool first.",
                            tool_calls=[
                                ChatCompletionMessageToolCall(
                                    id="call_1",
                                    type="function",
                                    function=Function(
                                        name="raising_tool",
                                        arguments='{"value": "test"}',
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
        else:
            # Second call: respond with finish tool
            return ModelResponse(
                id="mock-response-2",
                choices=[
                    Choices(
                        index=0,
                        message=LiteLLMMessage(
                            role="assistant",
                            content=None,
                            tool_calls=[
                                ChatCompletionMessageToolCall(
                                    id="finish-call-1",
                                    type="function",
                                    function=Function(
                                        name="finish",
                                        arguments=(
                                            '{"message": "I see there '
                                            'was an error. Task completed."}'
                                        ),
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

    collected_events = []

    def event_callback(event):
        collected_events.append(event)

    conversation = Conversation(agent=agent, callbacks=[event_callback])

    with patch(
        "openhands.sdk.llm.llm.litellm_completion", side_effect=mock_llm_response
    ):
        conversation.send_message(
            Message(
                role="user",
                content=[TextContent(text="Please help me.")],
            )
        )

        # Run first step - should generate error
        agent.step(conversation, on_event=event_callback)

        # Verify we got an error event
        error_events = [e for e in collected_events if isinstance(e, AgentErrorEvent)]
        assert len(error_events) == 1

        # Verify conversation is not finished
        with conversation.state:
            assert (
                conversation.state.execution_status
                != ConversationExecutionStatus.FINISHED
            )

        # Run second step - should call finish tool
        agent.step(conversation, on_event=event_callback)

        # Verify we got an action event for the finish tool
        action_events = [
            e
            for e in collected_events
            if isinstance(e, ActionEvent)
            and e.source == "agent"
            and e.tool_name == "finish"
        ]
        assert len(action_events) == 1

        # Now the conversation should be finished
        with conversation.state:
            assert (
                conversation.state.execution_status
                == ConversationExecutionStatus.FINISHED
            )

    # Verify we made two LLM calls
    assert call_count == 2
