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
        (Status.ERROR, Status.FINISHED),
        (Status.ERROR, Status.IDLE),
        (Status.FINISHED, Status.STUCK),
        (Status.FINISHED, Status.WAITING_FOR_CONFIRMATION),
        (Status.PAUSED, Status.IDLE),
        (Status.STUCK, Status.FINISHED),
        (Status.DELETING, Status.RUNNING),
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


@pytest.mark.parametrize("target", [Status.ERROR, Status.PAUSED, Status.RUNNING])
def test_failure_pause_and_restart_reachable_from_every_live_status(target):
    for current, allowed in _ALLOWED_TRANSITIONS.items():
        if current in (target, Status.DELETING):
            continue
        assert target in allowed, f"{current.value} -> {target.value}"


def test_deleting_is_final():
    assert _ALLOWED_TRANSITIONS[Status.DELETING] == frozenset()
