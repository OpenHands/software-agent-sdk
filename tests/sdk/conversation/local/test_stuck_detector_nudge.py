"""Integration tests for the repeating action-error corrective nudge (#4331).

Before this fix, a repeating action-error pattern (same malformed tool call,
same validation error, N times in a row) went straight to a terminal STUCK
status. These tests drive a real `Conversation.run()` loop with a tool that
always errors, verifying the run loop now gives the model one explicit
corrective nudge before hard-terminating, and that it only terminates if the
identical action-error pair continues past that nudge.
"""

from collections.abc import Sequence
from typing import ClassVar

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import AgentErrorEvent, MessageEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


class _AlwaysErrorAction(Action):
    """Mock action for a tool call that always fails validation/execution."""

    command: str


class _AlwaysErrorObservation(Observation):
    result: str

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [TextContent(text=self.result)]


class _AlwaysErrorExecutor(ToolExecutor[_AlwaysErrorAction, _AlwaysErrorObservation]):
    """Executor that always raises, mirroring a missing-required-arg error."""

    def __call__(self, action: _AlwaysErrorAction, conversation=None):
        raise ValueError("`file_text` is required for command: create.")


class _AlwaysErrorTool(ToolDefinition[_AlwaysErrorAction, _AlwaysErrorObservation]):
    name: ClassVar[str] = "always_error_tool"

    @classmethod
    def create(cls, conv_state=None, *, executor: ToolExecutor, **params):
        return [
            cls(
                description="A tool that always errors",
                action_type=_AlwaysErrorAction,
                observation_type=_AlwaysErrorObservation,
                executor=executor,
            )
        ]


def _bad_tool_call(call_id: str) -> MessageToolCall:
    return MessageToolCall(
        id=call_id,
        name="always_error_tool",
        arguments='{"command": "create"}',
        origin="completion",
    )


def _bad_tool_call_message(call_id: str) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[_bad_tool_call(call_id)],
    )


def _make_conversation(
    scripted_messages: list[Message | Exception],
) -> LocalConversation:
    register_tool(
        "always_error_tool", _AlwaysErrorTool.create(executor=_AlwaysErrorExecutor())[0]
    )
    llm = TestLLM.from_messages(scripted_messages)
    agent = Agent(llm=llm, tools=[Tool(name="always_error_tool")])
    conversation = Conversation(agent=agent)
    assert isinstance(conversation, LocalConversation)
    return conversation


def test_run_nudges_before_going_stuck_on_repeating_action_error():
    """4 identical failing calls: nudge after the 3rd, hard STUCK after the 4th."""
    scripted_messages: list[Message | Exception] = [
        _bad_tool_call_message(f"call_{i}") for i in range(4)
    ]
    conversation = _make_conversation(scripted_messages)
    conversation.send_message(
        Message(role="user", content=[TextContent(text="Create /tmp/foo.py")])
    )
    conversation.run()

    assert conversation.state.execution_status == ConversationExecutionStatus.STUCK

    error_events = [
        e for e in conversation.state.events if isinstance(e, AgentErrorEvent)
    ]
    assert len(error_events) == 4

    nudges = [
        e
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "environment"
    ]
    assert len(nudges) == 1
    nudge_text = nudges[0].llm_message.content[0]
    assert isinstance(nudge_text, TextContent)
    assert "always_error_tool" in nudge_text.text
    assert "file_text" in nudge_text.text

    # The nudge must land right after the 3rd error and before the 4th action.
    events = list(conversation.state.events)
    nudge_index = events.index(nudges[0])
    preceding_errors = [
        e for e in events[:nudge_index] if isinstance(e, AgentErrorEvent)
    ]
    following_errors = [
        e for e in events[nudge_index:] if isinstance(e, AgentErrorEvent)
    ]
    assert len(preceding_errors) == 3
    assert len(following_errors) == 1


def test_run_recovers_after_nudge_when_model_self_corrects():
    """3 identical failing calls then a different response: no STUCK at all."""
    scripted_messages: list[Message | Exception] = [
        *[_bad_tool_call_message(f"call_{i}") for i in range(3)],
        Message(
            role="assistant",
            content=[TextContent(text="I see the issue, stopping here.")],
        ),
    ]
    conversation = _make_conversation(scripted_messages)
    conversation.send_message(
        Message(role="user", content=[TextContent(text="Create /tmp/foo.py")])
    )
    conversation.run()

    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED

    error_events = [
        e for e in conversation.state.events if isinstance(e, AgentErrorEvent)
    ]
    assert len(error_events) == 3

    nudges = [
        e
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "environment"
    ]
    assert len(nudges) == 1
