"""Configured thresholds must fit in the detector's bounded event window."""

import json

import pytest

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, AgentErrorEvent, MessageEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM


def _think_message(
    index: int, thought: str | None, *, calls_per_response: int = 1
) -> Message:
    arguments = {} if thought is None else {"thought": thought}
    return Message(
        role="assistant",
        content=[],
        tool_calls=[
            MessageToolCall(
                id=f"call_{index}_{call_index}",
                name="think",
                arguments=json.dumps(arguments),
                origin="completion",
            )
            for call_index in range(calls_per_response)
        ],
    )


def _finished_message() -> Message:
    return Message(role="assistant", content=[TextContent(text="Done.")])


@pytest.mark.parametrize(
    ("pattern", "calls_per_response"),
    [("identical", 1), ("alternating", 1), ("error", 1), ("error", 2)],
)
def test_run_detects_loops_with_thresholds_beyond_ten_pairs(
    pattern, calls_per_response
):
    repetitions = 13 if calls_per_response == 2 else 12
    messages = [
        _think_message(
            i,
            None
            if pattern == "error"
            else str(i % 2 if pattern == "alternating" else 0),
            calls_per_response=calls_per_response,
        )
        for i in range(repetitions)
    ]
    conversation = Conversation(
        agent=Agent(
            llm=TestLLM.from_messages([*messages, _finished_message()]),
            include_default_tools=["FinishTool", "ThinkTool"],
        ),
        stuck_detection_thresholds={
            "action_observation": repetitions
            if pattern == "identical"
            else repetitions + 2,
            "action_error": repetitions - 1,
            "alternating_pattern": repetitions
            if pattern == "alternating"
            else repetitions + 2,
        },
    )
    conversation.send_message("Start")
    conversation.run()

    assert conversation.state.execution_status == ConversationExecutionStatus.STUCK
    events = list(conversation.state.events)
    assert sum(isinstance(event, ActionEvent) for event in events) == (
        repetitions * calls_per_response
    )
    if pattern == "error":
        assert sum(isinstance(event, AgentErrorEvent) for event in events) == (
            repetitions * calls_per_response
        )
        nudges = [
            event
            for event in events
            if isinstance(event, MessageEvent) and event.source == "environment"
        ]
        assert len(nudges) == 1
        nudge_index = events.index(nudges[0])
        assert (
            sum(isinstance(event, AgentErrorEvent) for event in events[:nudge_index])
            == (repetitions - 1) * calls_per_response
        )


def test_run_detects_large_monologue_threshold_with_corrective_nudges():
    conversation = Conversation(
        agent=Agent(
            llm=TestLLM.from_messages(
                [
                    *[Message(role="assistant", content=[]) for _ in range(12)],
                    _finished_message(),
                ]
            )
        ),
        stuck_detection_thresholds={"monologue": 12},
    )
    conversation.send_message("Start")
    conversation.run()

    assert conversation.state.execution_status == ConversationExecutionStatus.STUCK
    assert (
        sum(
            isinstance(event, MessageEvent) and event.source == "agent"
            for event in conversation.state.events
        )
        == 12
    )


@pytest.mark.parametrize("new_user_message", [False, True])
def test_large_threshold_does_not_stop_before_threshold_or_across_user_message(
    new_user_message,
):
    messages: list[Message | Exception] = [_think_message(i, "same") for i in range(11)]
    messages.append(_finished_message())
    if new_user_message:
        messages.extend([_think_message(11, "same"), _finished_message()])
    conversation = Conversation(
        agent=Agent(
            llm=TestLLM.from_messages(messages),
            include_default_tools=["FinishTool", "ThinkTool"],
        ),
        stuck_detection_thresholds={
            "action_observation": 12,
            "alternating_pattern": 14,
        },
    )
    conversation.send_message("Start")
    conversation.run()
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    if new_user_message:
        conversation.send_message("Try once more")
        conversation.run()
        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
