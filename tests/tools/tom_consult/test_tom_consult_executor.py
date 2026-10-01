"""Tests for Tom sleeptime processing checkpoints."""

import json
from typing import Any, cast
from unittest.mock import Mock

import pytest

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event import MessageEvent
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.llm import Message, TextContent
from openhands.tools.tom_consult.definition import (
    SleeptimeComputeAction,
    SleeptimeComputeObservation,
)
from openhands.tools.tom_consult.executor import TomConsultExecutor


HISTORY_DIR = "user-model"
HISTORY_FILE = f"{HISTORY_DIR}/processed_sessions_timestamps.json"


def _make_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TomConsultExecutor, Mock, InMemoryFileStore]:
    monkeypatch.setattr(
        "tom_swe.memory.locations.get_usermodeling_dir",
        lambda _user_id: HISTORY_DIR,
    )
    file_store = InMemoryFileStore()
    tom_agent = Mock()
    executor = TomConsultExecutor(file_store)
    executor._tom_agent = cast(Any, tom_agent)
    return executor, tom_agent, file_store


def _append_user_message(
    file_store: InMemoryFileStore,
    session_id: str,
    event_id: str,
    text: str,
) -> None:
    event_log = EventLog(file_store, f"conversations/{session_id}/events")
    event_log.append(
        MessageEvent(
            id=event_id,
            llm_message=Message(
                role="user",
                content=[TextContent(text=text)],
            ),
            source="user",
        )
    )


def test_sleeptime_checkpoint_preserves_pre_submission_file_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, tom_agent, file_store = _make_executor(monkeypatch)
    session_id = "session-1"
    events_dir = f"conversations/{session_id}/events"
    _append_user_message(file_store, session_id, "00000000", "first")
    file_store.write(f"{events_dir}/metadata.json", "{}")

    def append_during_tom_call(**_kwargs: Any) -> None:
        _append_user_message(file_store, session_id, "11111111", "second")

    tom_agent.sleeptime_compute.side_effect = append_during_tom_call

    first_result = executor(SleeptimeComputeAction())

    assert isinstance(first_result, SleeptimeComputeObservation)
    assert first_result.sessions_processed == 1
    first_payload = tom_agent.sleeptime_compute.call_args.kwargs["sessions_data"]
    assert first_payload[0]["event_count"] == 1
    history = json.loads(file_store.read(HISTORY_FILE))
    assert history[session_id]["last_event_count"] == 2
    assert len(file_store.list(events_dir)) == 3

    tom_agent.sleeptime_compute.reset_mock(side_effect=True)

    second_result = executor(SleeptimeComputeAction())

    assert isinstance(second_result, SleeptimeComputeObservation)
    assert second_result.sessions_processed == 1
    second_payload = tom_agent.sleeptime_compute.call_args.kwargs["sessions_data"]
    assert second_payload[0]["event_count"] == 2


def test_sleeptime_does_not_checkpoint_session_without_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, tom_agent, file_store = _make_executor(monkeypatch)
    _append_user_message(file_store, "valid-session", "00000000", "hello")
    file_store.write("conversations/empty-session/events/metadata.json", "{}")

    result = executor(SleeptimeComputeAction())

    assert isinstance(result, SleeptimeComputeObservation)
    assert result.sessions_processed == 1
    payload = tom_agent.sleeptime_compute.call_args.kwargs["sessions_data"]
    assert [session["session_id"] for session in payload] == ["valid-session"]
    history = json.loads(file_store.read(HISTORY_FILE))
    assert set(history) == {"valid-session"}


def test_sleeptime_failure_does_not_advance_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, tom_agent, file_store = _make_executor(monkeypatch)
    session_id = "session-1"
    _append_user_message(file_store, session_id, "00000000", "hello")
    existing_history = {
        session_id: {
            "processed_at": "2026-08-01T00:00:00",
            "last_event_count": 0,
        }
    }
    file_store.write(HISTORY_FILE, json.dumps(existing_history))
    tom_agent.sleeptime_compute.side_effect = RuntimeError("Tom failed")

    with pytest.raises(RuntimeError, match="Tom failed"):
        executor(SleeptimeComputeAction())

    assert json.loads(file_store.read(HISTORY_FILE)) == existing_history
