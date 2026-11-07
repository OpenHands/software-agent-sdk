"""Tests for ConversationState.transition and its status table."""

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import (
    _ALLOWED_TRANSITIONS,
    ConversationExecutionStatus,
    ConversationState,
    InvalidExecutionStatusTransitionError,
)
from openhands.sdk.event import Event
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.llm import LLM


Status = ConversationExecutionStatus


@pytest.fixture
def state(tmp_path) -> ConversationState:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    conversation = Conversation(
        agent=Agent(llm=llm, tools=[]), persistence_dir=str(tmp_path)
    )
    assert isinstance(conversation, LocalConversation)
    return conversation._state


def _status_updates(events: list[Event]) -> list[object]:
    return [
        event.value
        for event in events
        if isinstance(event, ConversationStateUpdateEvent)
        and event.key == "execution_status"
    ]


def test_allowed_transition_publishes_update(state):
    events: list[Event] = []
    state.set_on_state_change(events.append)

    state.transition(Status.RUNNING, "run started")

    assert state.execution_status == Status.RUNNING
    assert _status_updates(events) == [Status.RUNNING]


def test_transition_to_current_status_is_noop(state):
    events: list[Event] = []
    state.set_on_state_change(events.append)

    state.transition(Status.IDLE, "already idle")

    assert state.execution_status == Status.IDLE
    assert _status_updates(events) == []


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Status.ERROR, Status.IDLE),
        (Status.FINISHED, Status.STUCK),
        (Status.PAUSED, Status.STUCK),
    ],
)
def test_rejected_transition_raises_and_keeps_status(state, current, target):
    state.execution_status = current

    with pytest.raises(InvalidExecutionStatusTransitionError) as exc_info:
        state.transition(target, "test")

    assert state.execution_status == current
    assert (exc_info.value.current, exc_info.value.target) == (current, target)


def test_table_covers_every_status():
    assert set(_ALLOWED_TRANSITIONS) == set(Status)


@pytest.mark.parametrize(
    "target",
    [
        # The run loop's exception handlers write these; they must never raise.
        Status.ERROR,
        Status.PAUSED,
        Status.RUNNING,
        # A step can run under any status (condense(), a direct agent.step(),
        # the sync loop retrying after a turn reported ERROR).
        Status.FINISHED,
        Status.WAITING_FOR_CONFIRMATION,
    ],
)
def test_run_and_step_outcomes_reachable_from_every_status(target):
    for current, allowed in _ALLOWED_TRANSITIONS.items():
        if current is not target:
            assert target in allowed, f"{current.value} -> {target.value}"


@pytest.mark.parametrize(
    ("current", "target"),
    [
        # Stuck detection after the sync loop retried a turn ACP reported as
        # a timeout (ERROR).
        (Status.ERROR, Status.STUCK),
        # pause() landing between the ACP branch's two lock sections, then a
        # message arriving on the final iteration.
        (Status.PAUSED, Status.IDLE),
        # A foreign writer left DELETING in base_state.json; the error handler
        # must still record the failure.
        (Status.DELETING, Status.ERROR),
    ],
)
def test_moves_found_in_review_stay_allowed(state, current, target):
    state.execution_status = current

    state.transition(target, "test")

    assert state.execution_status == target
