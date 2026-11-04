import uuid

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.stuck_detector import (
    CONTEXT_WINDOW_ERROR_LOOP_THRESHOLD,
    StuckDetector,
)
from openhands.sdk.event import (
    ActionEvent,
    Condensation,
    CondensationRequest,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal.definition import TerminalAction, TerminalObservation


def _state() -> ConversationState:
    agent = Agent(llm=LLM(model="gpt-4o-mini", usage_id="test-llm"))
    return ConversationState.create(
        id=uuid.uuid4(), agent=agent, workspace=LocalWorkspace(working_dir="/tmp")
    )


def _user(text="Fix the bug"):
    return MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=text)]),
    )


def _condensation(i: int) -> Condensation:
    return Condensation(forgotten_event_ids=set(), llm_response_id=f"summary_{i}")


def _action_observation(i: int) -> list:
    action = ActionEvent(
        source="agent",
        thought=[TextContent(text="next step")],
        action=TerminalAction(command=f"echo {i}"),
        tool_name="terminal",
        tool_call_id=f"call_{i}",
        tool_call=MessageToolCall(
            id=f"call_{i}",
            name="terminal",
            arguments=f'{{"command": "echo {i}"}}',
            origin="completion",
        ),
        llm_response_id=f"response_{i}",
    )
    observation = ObservationEvent(
        source="environment",
        observation=TerminalObservation.from_text(
            text=str(i), command=f"echo {i}", exit_code=0
        ),
        action_id=action.id,
        tool_name="terminal",
        tool_call_id=f"call_{i}",
    )
    return [action, observation]


def _failed_cycles(n: int, start: int = 0) -> list:
    # The LLM rejects the context, the condenser shrinks it, the LLM rejects it again.
    events = [CondensationRequest()]
    for i in range(start, start + n):
        events += [_condensation(i), CondensationRequest()]
    return events


def _detector(events) -> StuckDetector:
    state = _state()
    for event in [_user(), *events]:
        state.events.append(event)
    return StuckDetector(state)


def test_condensation_that_never_fits_is_stuck():
    assert _detector(_failed_cycles(CONTEXT_WINDOW_ERROR_LOOP_THRESHOLD)).is_stuck()


def test_fewer_failed_condensations_than_threshold_is_not_stuck():
    events = _failed_cycles(CONTEXT_WINDOW_ERROR_LOOP_THRESHOLD - 1)
    assert not _detector(events).is_stuck()


def test_long_task_condensing_between_steps_is_not_stuck():
    # Legit long task: condensation keeps succeeding and the agent keeps working.
    events = []
    for i in range(10):
        events += [CondensationRequest(), _condensation(i), *_action_observation(i)]
    assert not _detector(events).is_stuck()


def test_back_to_back_condensations_without_request_are_not_stuck():
    # The condenser may shrink twice in a row by itself; no LLM call failed.
    events = [_condensation(i) for i in range(10)]
    assert not _detector(events).is_stuck()


def test_recovery_resets_the_count():
    threshold = CONTEXT_WINDOW_ERROR_LOOP_THRESHOLD
    events = [
        *_failed_cycles(threshold - 1),
        _condensation(99),
        *_action_observation(0),
        *_failed_cycles(threshold - 1, start=threshold),
    ]
    assert not _detector(events).is_stuck()


@pytest.mark.parametrize("separator", ["user", "action"])
def test_loop_broken_by_progress_or_user_is_not_stuck(separator):
    threshold = CONTEXT_WINDOW_ERROR_LOOP_THRESHOLD
    middle = [_user("try again")] if separator == "user" else _action_observation(0)
    events = [*_failed_cycles(threshold), *middle, *_failed_cycles(1, start=50)]
    assert not _detector(events).is_stuck()
