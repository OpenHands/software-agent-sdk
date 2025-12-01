"""Test agent behavior with reasoning-only responses (e.g., GPT-5 codex)."""

from unittest.mock import MagicMock

from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.llm_convertible.message import MessageEvent
from openhands.sdk.llm import LLM, LLMResponse, Message, MessageToolCall, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage


class ReasoningOnlyLLM(LLM):
    """Test LLM that returns reasoning-only response first, then finish."""

    _call_count: int = PrivateAttr(default=0)

    def __init__(self):
        super().__init__(model="test-model")

    def completion(  # type: ignore[override]
        self, *, messages, tools=None, **kwargs
    ) -> LLMResponse:
        self._call_count += 1

        if self._call_count == 1:
            # First call: return reasoning-only response
            message = Message(role="assistant")
            message.reasoning_content = "Let me think about this..."
            return LLMResponse(
                message=message,
                metrics=MetricsSnapshot(
                    model_name="test",
                    accumulated_cost=0.0,
                    max_budget_per_task=0.0,
                    accumulated_token_usage=TokenUsage(model="test"),
                ),
                raw_response=MagicMock(spec=ModelResponse, id="r1"),
            )
        else:
            # Second call: return finish action
            message = Message(role="assistant")
            message.tool_calls = [
                MessageToolCall(
                    id="finish-call-1",
                    name="finish",
                    arguments='{"message": "Task completed"}',
                    origin="completion",
                )
            ]
            return LLMResponse(
                message=message,
                metrics=MetricsSnapshot(
                    model_name="test",
                    accumulated_cost=0.0,
                    max_budget_per_task=0.0,
                    accumulated_token_usage=TokenUsage(model="test"),
                ),
                raw_response=MagicMock(spec=ModelResponse, id="r2"),
            )


def test_agent_continues_after_reasoning_only_response():
    """Test that agent continues looping after receiving reasoning-only response."""
    llm = ReasoningOnlyLLM()
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Send initial user message
    conversation.send_message("Please solve this task")

    # Run the conversation
    conversation.run()

    # Verify agent was called twice (reasoning-only, then finish)
    assert llm._call_count == 2

    # Verify conversation finished
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


class ContentOnlyLLM(LLM):
    """Test LLM that returns content-only response (should finish immediately)."""

    _call_count: int = PrivateAttr(default=0)

    def __init__(self):
        super().__init__(model="test-model")

    def completion(  # type: ignore[override]
        self, *, messages, tools=None, **kwargs
    ) -> LLMResponse:
        self._call_count += 1

        # Return content-only response - should finish conversation immediately
        message = Message(role="assistant")
        message.content = [TextContent(text="I'm thinking about this...")]
        return LLMResponse(
            message=message,
            metrics=MetricsSnapshot(
                model_name="test",
                accumulated_cost=0.0,
                max_budget_per_task=0.0,
                accumulated_token_usage=TokenUsage(model="test"),
            ),
            raw_response=MagicMock(spec=ModelResponse, id="r1"),
        )


def test_agent_finishes_after_content_only_response():
    """Test that agent finishes immediately after receiving content-only response."""
    llm = ContentOnlyLLM()
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    conversation.send_message("Analyze this")
    conversation.run()

    # Verify agent was called once - content responses finish immediately
    assert llm._call_count == 1
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED

    # Verify the content message was emitted
    msg_events = [
        e
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "agent"
    ]
    assert len(msg_events) == 1
    assert any(
        isinstance(c, TextContent) and c.text == "I'm thinking about this..."
        for c in msg_events[0].llm_message.content
    )


class EmptyResponseLLM(LLM):
    """Test LLM that returns empty response first, then finish."""

    _call_count: int = PrivateAttr(default=0)

    def __init__(self):
        super().__init__(model="test-model")

    def completion(  # type: ignore[override]
        self, *, messages, tools=None, **kwargs
    ) -> LLMResponse:
        self._call_count += 1

        if self._call_count == 1:
            # First call: return empty response (edge case)
            message = Message(role="assistant")
            message.content = []
            return LLMResponse(
                message=message,
                metrics=MetricsSnapshot(
                    model_name="test",
                    accumulated_cost=0.0,
                    max_budget_per_task=0.0,
                    accumulated_token_usage=TokenUsage(model="test"),
                ),
                raw_response=MagicMock(spec=ModelResponse, id="r1"),
            )
        else:
            # Second call: return finish action
            message = Message(role="assistant")
            message.tool_calls = [
                MessageToolCall(
                    id="finish-call-3",
                    name="finish",
                    arguments='{"message": "Done"}',
                    origin="completion",
                )
            ]
            return LLMResponse(
                message=message,
                metrics=MetricsSnapshot(
                    model_name="test",
                    accumulated_cost=0.0,
                    max_budget_per_task=0.0,
                    accumulated_token_usage=TokenUsage(model="test"),
                ),
                raw_response=MagicMock(spec=ModelResponse, id="r2"),
            )


def test_agent_handles_empty_response():
    """Test that agent continues even with completely empty response."""
    llm = EmptyResponseLLM()
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    conversation.send_message("Test")
    conversation.run()

    # Verify agent continued after empty response
    assert llm._call_count == 2
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
