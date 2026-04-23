"""Unit tests for LLM response classification and dispatch."""

from unittest.mock import MagicMock

import pytest
from litellm.types.utils import ModelResponse

from openhands.sdk.agent import Agent
from openhands.sdk.agent.response_dispatch import LLMResponseType, classify_response
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, Event, MessageEvent
from openhands.sdk.llm import (
    LLM,
    LLMResponse,
    Message,
    MessageToolCall,
    ReasoningItemModel,
    RedactedThinkingBlock,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage


def _msg(**kwargs) -> Message:
    """Shorthand to build a Message with defaults."""
    return Message(role="assistant", **kwargs)


def _tool_call() -> MessageToolCall:
    return MessageToolCall(id="tc1", name="bash", arguments="{}", origin="completion")


# ---------------------------------------------------------------------------
# classify_response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(
            dict(
                tool_calls=[_tool_call()],
                content=[TextContent(text="Let me run this")],
                reasoning_content="I should use bash",
            ),
            id="row1-tools+content+reasoning",
        ),
        pytest.param(
            dict(
                tool_calls=[_tool_call()],
                content=[TextContent(text="Running command")],
            ),
            id="row2-tools+content",
        ),
        pytest.param(
            dict(tool_calls=[_tool_call()], reasoning_content="Thinking about it..."),
            id="row3-tools+reasoning",
        ),
        pytest.param(
            dict(tool_calls=[_tool_call()]),
            id="row4-tools-only",
        ),
        pytest.param(
            dict(tool_calls=[_tool_call()], content=[]),
            id="tools-with-empty-content",
        ),
    ],
)
def test_tool_calls_response(kwargs):
    """Any message with tool_calls classifies as TOOL_CALLS."""
    assert classify_response(_msg(**kwargs)) == LLMResponseType.TOOL_CALLS


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(
            dict(
                content=[TextContent(text="The answer is 42")],
                reasoning_content="Let me calculate...",
            ),
            id="row5-content+reasoning",
        ),
        pytest.param(
            dict(content=[TextContent(text="Hello world")]),
            id="row6-content-only",
        ),
        pytest.param(
            dict(
                content=[TextContent(text="Here is my answer")],
                thinking_blocks=[
                    ThinkingBlock(thinking="Let me think", signature="sig")
                ],
            ),
            id="content-with-thinking-blocks",
        ),
    ],
)
def test_content_response(kwargs):
    """No tool_calls + non-blank TextContent classifies as CONTENT."""
    assert classify_response(_msg(**kwargs)) == LLMResponseType.CONTENT


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(
            dict(reasoning_content="Let me think about this..."),
            id="row7a-reasoning-content",
        ),
        pytest.param(
            dict(
                content=[],
                thinking_blocks=[
                    ThinkingBlock(thinking="The answer is 2", signature="sig-1")
                ],
            ),
            id="row7b-thinking-blocks",
        ),
        pytest.param(
            dict(
                content=[],
                thinking_blocks=[RedactedThinkingBlock(data="encrypted")],
            ),
            id="row7c-redacted-thinking",
        ),
        pytest.param(
            dict(
                content=[],
                responses_reasoning_item=ReasoningItemModel(
                    id="ri-1", summary=["thinking"]
                ),
            ),
            id="row7d-responses-reasoning-item",
        ),
    ],
)
def test_reasoning_only_response(kwargs):
    """No tool_calls, no visible content, but reasoning classifies as REASONING_ONLY."""
    assert classify_response(_msg(**kwargs)) == LLMResponseType.REASONING_ONLY


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(dict(content=[]), id="row8-empty-content"),
        pytest.param(
            dict(content=[TextContent(text="   \n  ")]),
            id="whitespace-only-content",
        ),
        pytest.param(
            dict(content=[], thinking_blocks=[]),
            id="empty-content-and-thinking-blocks",
        ),
    ],
)
def test_empty_response(kwargs):
    """No tool_calls, no content, no reasoning classifies as EMPTY."""
    assert classify_response(_msg(**kwargs)) == LLMResponseType.EMPTY


# ---------------------------------------------------------------------------
# ResponseDispatchMixin (via Agent integration)
# ---------------------------------------------------------------------------


def _make_metrics() -> MetricsSnapshot:
    return MetricsSnapshot(
        model_name="test",
        accumulated_cost=0.0,
        max_budget_per_task=0.0,
        accumulated_token_usage=TokenUsage(model="test"),
    )


def _make_llm_response(message: Message) -> LLMResponse:
    return LLMResponse(
        message=message,
        metrics=_make_metrics(),
        raw_response=MagicMock(spec=ModelResponse, id="r1"),
    )


def _run_single_step(
    llm_response: LLMResponse,
) -> tuple[list[Event], LocalConversation]:
    """Run one agent step with a canned LLM response."""
    from pydantic import PrivateAttr

    class SingleShotLLM(LLM):
        _response: LLMResponse = PrivateAttr()

        def __init__(self, response: LLMResponse):
            super().__init__(model="test-model")
            self._response = response

        def completion(  # type: ignore[override]
            self, *, messages, tools=None, **kwargs
        ) -> LLMResponse:
            return self._response

    llm = SingleShotLLM(llm_response)
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)
    conversation._ensure_agent_ready()

    events: list[Event] = []

    def on_event(e: Event) -> None:
        events.append(e)

    agent.step(conversation, on_event=on_event)
    return events, conversation


def test_content_response_sets_finished():
    """_handle_content_response sets execution status to FINISHED."""
    msg = Message(role="assistant", content=[TextContent(text="Done!")])
    events, convo = _run_single_step(_make_llm_response(msg))
    msg_events = [e for e in events if isinstance(e, MessageEvent)]

    assert convo.state.execution_status == ConversationExecutionStatus.FINISHED
    assert len(msg_events) == 1
    assert msg_events[0].source == "agent"


def test_empty_response_sends_nudge():
    """_handle_no_content_response emits agent message + corrective nudge."""
    msg = Message(role="assistant", content=[])
    events, convo = _run_single_step(_make_llm_response(msg))
    msg_events = [e for e in events if isinstance(e, MessageEvent)]

    assert convo.state.execution_status != ConversationExecutionStatus.FINISHED
    assert len(msg_events) == 2
    assert msg_events[0].source == "agent"
    assert msg_events[1].source == "user"
    nudge_content = msg_events[1].llm_message.content[0]
    assert isinstance(nudge_content, TextContent)
    assert "function call" in nudge_content.text


def test_reasoning_only_sends_nudge():
    """_handle_no_content_response sends corrective nudge for reasoning-only."""
    msg = Message(role="assistant", reasoning_content="Let me think...")
    events, convo = _run_single_step(_make_llm_response(msg))
    msg_events = [e for e in events if isinstance(e, MessageEvent)]

    assert convo.state.execution_status != ConversationExecutionStatus.FINISHED
    assert len(msg_events) == 2
    assert msg_events[0].source == "agent"
    assert msg_events[1].source == "user"


def test_tool_calls_response_executes_actions():
    """_handle_tool_calls creates and executes action events."""
    tool_call = MessageToolCall(
        id="tc-finish",
        name="finish",
        arguments='{"message": "All done"}',
        origin="completion",
    )
    msg = Message(
        role="assistant",
        tool_calls=[tool_call],
        content=[TextContent(text="Finishing up")],
    )
    events, convo = _run_single_step(_make_llm_response(msg))
    action_events = [e for e in events if isinstance(e, ActionEvent)]

    assert len(action_events) == 1
    assert action_events[0].tool_call_id == "tc-finish"
    assert convo.state.execution_status == ConversationExecutionStatus.FINISHED
