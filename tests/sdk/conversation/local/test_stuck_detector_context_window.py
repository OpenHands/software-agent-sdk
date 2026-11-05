"""Integration tests for the context-window error stuck loop detection (#6104).

Every agent LLM call is rejected with a context-window error while the
summarizer condenser succeeds, so the conversation can only cycle through
``CondensationRequest``/``Condensation`` pairs without progress.
"""

import tempfile

from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import CondensationRequest, MessageEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.exceptions import LLMContextWindowExceedError
from openhands.sdk.testing import TestLLM


# Enough scripted responses to outlast max_iteration_per_run when detection
# is disabled, so exhaustion never ends the run first.
_SCRIPTED_RESPONSES = 40
_MAX_ITERATION_PER_RUN = 30


def _make_conversation(
    *,
    stuck_detection: bool,
) -> tuple[LocalConversation, TestLLM]:
    agent_llm = TestLLM.from_messages(
        [
            LLMContextWindowExceedError("prompt is too long")
            for _ in range(_SCRIPTED_RESPONSES)
        ]
    )
    summarizer_llm = TestLLM.from_messages(
        [
            Message(role="assistant", content=[TextContent(text="condensed history")])
            for _ in range(_SCRIPTED_RESPONSES)
        ]
    )
    agent = Agent(
        llm=agent_llm,
        tools=[],
        condenser=LLMSummarizingCondenser(
            llm=summarizer_llm, max_size=240, keep_first=2
        ),
    )
    conversation = Conversation(
        agent=agent,
        workspace=tempfile.mkdtemp(),
        visualizer=None,
        max_iteration_per_run=_MAX_ITERATION_PER_RUN,
        stuck_detection=stuck_detection,
    )
    assert isinstance(conversation, LocalConversation)
    return conversation, agent_llm


def _seed_condensable_history(conversation: LocalConversation) -> None:
    """Leave a final user message so the stuck window holds only the cycles."""
    conversation.send_message(Message(role="user", content=[TextContent(text="start")]))
    for i in range(30):
        conversation.state.append_event(
            MessageEvent(
                source="user" if i % 2 == 0 else "agent",
                llm_message=Message(
                    role="user" if i % 2 == 0 else "assistant",
                    content=[TextContent(text=f"old {i} " + "x" * 200)],
                ),
            )
        )
    conversation.send_message(
        Message(role="user", content=[TextContent(text="continue")])
    )


def test_context_window_error_loop_ends_stuck():
    """The rejected-call/condensation cycle is detected before max iterations."""
    conversation, agent_llm = _make_conversation(stuck_detection=True)
    _seed_condensable_history(conversation)

    conversation.run()

    assert conversation.state.execution_status == ConversationExecutionStatus.STUCK
    error_events = [
        e for e in conversation.state.events if isinstance(e, ConversationErrorEvent)
    ]
    assert not error_events
    requests = [
        e for e in conversation.state.events if isinstance(e, CondensationRequest)
    ]
    assert requests
    # The run ended well before burning the iteration budget.
    assert agent_llm.call_count < _MAX_ITERATION_PER_RUN


def test_stuck_detection_disabled_context_window_loop_ends_error():
    """Without stuck detection the same loop runs to MaxIterationsReached."""
    conversation, _ = _make_conversation(stuck_detection=False)
    _seed_condensable_history(conversation)

    conversation.run()

    assert conversation.state.execution_status == ConversationExecutionStatus.ERROR
    error_events = [
        e for e in conversation.state.events if isinstance(e, ConversationErrorEvent)
    ]
    assert error_events
