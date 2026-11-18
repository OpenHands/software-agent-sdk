"""The gateway's wait on a continued conversation stops where a new one stops."""

import asyncio
from types import SimpleNamespace

import pytest

from openhands.agent_server.errors import status_for
from openhands.agent_server.openai import service
from openhands.agent_server.openai.service import (
    _raise_for_terminal_error,
    _wait_for_reused_conversation_completion,
)
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.errors import DomainError


class _StatusSequence:
    """Stands in for EventService: each get_state() returns the next status."""

    def __init__(
        self,
        *statuses: ConversationExecutionStatus,
        events: int = 0,
        polls_with_live_run: int = 0,
    ):
        self._statuses = list(statuses)
        self._events = events
        self._polls_with_live_run = polls_with_live_run
        self._run_task: asyncio.Future | None = None

    async def get_state(self):
        if self._polls_with_live_run > 0:
            self._polls_with_live_run -= 1
            self._run_task = asyncio.get_running_loop().create_future()
        else:
            self._run_task = None
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return SimpleNamespace(execution_status=status, events=[None] * self._events)


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(service, "_POLL_INTERVAL_SECONDS", 0)


@pytest.mark.parametrize(
    "stopped",
    [
        ConversationExecutionStatus.WAITING_FOR_CONFIRMATION,
        ConversationExecutionStatus.PAUSED,
    ],
)
def test_a_continued_run_that_stops_for_the_user_answers_409_at_once(stopped):
    events = _StatusSequence(ConversationExecutionStatus.RUNNING, stopped)

    status_value = asyncio.run(
        _wait_for_reused_conversation_completion(
            events,  # type: ignore[arg-type]
            min_event_count=10,
            timeout_seconds=5,
        )
    )

    assert status_value == stopped
    with pytest.raises(DomainError) as exc_info:
        _raise_for_terminal_error(status_value)
    assert status_for(exc_info.value) == 409
    assert exc_info.value.detail == f"Agent run ended with status: {stopped.value}"


def test_a_conversation_left_waiting_before_the_new_turn_is_not_the_answer():
    # The run for this turn has not started yet: no RUNNING seen and no new
    # events, so the earlier turn's waiting status must not end the wait.
    events = _StatusSequence(ConversationExecutionStatus.WAITING_FOR_CONFIRMATION)

    with pytest.raises(DomainError) as exc_info:
        asyncio.run(
            _wait_for_reused_conversation_completion(
                events,  # type: ignore[arg-type]
                min_event_count=10,
                timeout_seconds=0,
            )
        )
    assert status_for(exc_info.value) == 504


@pytest.mark.parametrize(
    "left_behind",
    [ConversationExecutionStatus.PAUSED, ConversationExecutionStatus.ERROR],
)
def test_a_status_left_by_the_previous_run_is_not_the_answer_while_the_turn_runs(
    left_behind,
):
    # The turn's message and state updates are already saved and its run is
    # scheduled, but the status still says how the previous run ended.
    events = _StatusSequence(
        left_behind,
        ConversationExecutionStatus.FINISHED,
        events=12,
        polls_with_live_run=1,
    )

    status_value = asyncio.run(
        _wait_for_reused_conversation_completion(
            events,  # type: ignore[arg-type]
            min_event_count=10,
            timeout_seconds=5,
        )
    )

    assert status_value == ConversationExecutionStatus.FINISHED


def test_a_turn_paused_while_it_runs_still_answers_409():
    events = _StatusSequence(
        ConversationExecutionStatus.PAUSED,
        ConversationExecutionStatus.RUNNING,
        ConversationExecutionStatus.PAUSED,
        events=12,
        polls_with_live_run=2,
    )

    status_value = asyncio.run(
        _wait_for_reused_conversation_completion(
            events,  # type: ignore[arg-type]
            min_event_count=10,
            timeout_seconds=5,
        )
    )

    assert status_value == ConversationExecutionStatus.PAUSED
    with pytest.raises(DomainError) as exc_info:
        _raise_for_terminal_error(status_value)
    assert status_for(exc_info.value) == 409
