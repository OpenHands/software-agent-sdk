"""The gateway's wait on a continued conversation stops where a new one stops."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openhands.agent_server.openai import service
from openhands.agent_server.openai.service import (
    _raise_for_terminal_error,
    _wait_for_reused_conversation_completion,
)
from openhands.sdk.conversation.state import ConversationExecutionStatus


class _StatusSequence:
    """Stands in for EventService: each get_state() returns the next status."""

    def __init__(self, *statuses: ConversationExecutionStatus, events: int = 0):
        self._statuses = list(statuses)
        self._events = events

    async def get_state(self):
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return SimpleNamespace(execution_status=status, events=[None] * self._events)


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(service, "_POLL_INTERVAL_SECONDS", 0)


@pytest.mark.parametrize(
    "active",
    [ConversationExecutionStatus.RUNNING, ConversationExecutionStatus.PAUSING],
)
@pytest.mark.parametrize(
    "stopped",
    [
        ConversationExecutionStatus.WAITING_FOR_CONFIRMATION,
        ConversationExecutionStatus.PAUSED,
    ],
)
def test_a_continued_run_that_stops_for_the_user_answers_409_at_once(stopped, active):
    # A pending pause (PAUSING) is still an observed run for this turn.
    events = _StatusSequence(active, stopped)

    status_value = asyncio.run(
        _wait_for_reused_conversation_completion(
            events,  # type: ignore[arg-type]
            min_event_count=10,
            timeout_seconds=5,
        )
    )

    assert status_value == stopped
    with pytest.raises(HTTPException) as exc_info:
        _raise_for_terminal_error(status_value)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == f"Agent run ended with status: {stopped.value}"


def test_a_conversation_left_waiting_before_the_new_turn_is_not_the_answer():
    # The run for this turn has not started yet: no RUNNING seen and no new
    # events, so the earlier turn's waiting status must not end the wait.
    events = _StatusSequence(ConversationExecutionStatus.WAITING_FOR_CONFIRMATION)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            _wait_for_reused_conversation_completion(
                events,  # type: ignore[arg-type]
                min_event_count=10,
                timeout_seconds=0,
            )
        )
    assert exc_info.value.status_code == 504
