"""Tests for the UnknownEvent fallback in EventLog deserialization."""

import json

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import Function

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.persistence_const import EVENT_FILE_PATTERN
from openhands.sdk.event import ActionEvent, ObservationEvent, UnknownEvent
from openhands.sdk.io.memory import InMemoryFileStore
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.tool.schema import Action, Observation


class ActionRegisteredForTest(Action):
    command: str


class ObservationRegisteredForTest(Observation):
    result: str


def _write(fs: InMemoryFileStore, idx: int, event_id: str, payload: dict | str) -> None:
    path = f"events/{EVENT_FILE_PATTERN.format(idx=idx, event_id=event_id)}"
    fs.write(path, payload if isinstance(payload, str) else json.dumps(payload))


def _make_action_event(call_id: str) -> ActionEvent:
    tool_call = MessageToolCall.from_chat_tool_call(
        ChatCompletionMessageToolCall(
            id=call_id,
            type="function",
            function=Function(name="test_tool", arguments='{"command": "ls"}'),
        )
    )
    return ActionEvent(
        source="agent",
        thought=[TextContent(text="think")],
        action=ActionRegisteredForTest(command="ls"),
        tool_name="test_tool",
        tool_call_id=call_id,
        tool_call=tool_call,
        llm_response_id="resp_1",
    )


def test_action_event_with_removed_nested_action():
    fs = InMemoryFileStore()
    real = _make_action_event("call_removed")
    payload = json.loads(real.model_dump_json(exclude_none=True))
    payload["action"]["kind"] = "RemovedFooAction"
    _write(fs, 0, real.id, payload)

    loaded = EventLog(fs)[0]

    assert isinstance(loaded, UnknownEvent)
    assert loaded.id == real.id
    assert loaded.tool_call_id == "call_removed"
    assert loaded.llm_response_id == "resp_1"
    assert loaded.tool_name == "test_tool"
    assert loaded.action_id is None
    assert loaded.original_kind == "ActionEvent"
    assert loaded.original_data["action"]["kind"] == "RemovedFooAction"


def test_observation_event_with_removed_nested_observation():
    fs = InMemoryFileStore()
    action = _make_action_event("call_2")
    observation = ObservationEvent(
        source="environment",
        observation=ObservationRegisteredForTest(result="ok"),
        action_id=action.id,
        tool_name="test_tool",
        tool_call_id="call_2",
    )
    payload = json.loads(observation.model_dump_json(exclude_none=True))
    payload["observation"]["kind"] = "RemovedFooObservation"
    _write(fs, 0, observation.id, payload)

    loaded = EventLog(fs)[0]

    assert isinstance(loaded, UnknownEvent)
    assert loaded.id == observation.id
    assert loaded.tool_call_id == "call_2"
    assert loaded.action_id == action.id
    assert loaded.llm_response_id is None
    assert loaded.original_kind == "ObservationEvent"


def test_unknown_top_level_event_kind():
    fs = InMemoryFileStore()
    event_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    _write(
        fs,
        0,
        event_id,
        {
            "id": event_id,
            "timestamp": "2026-04-24T00:00:00",
            "source": "agent",
            "kind": "CompletelyRemovedEvent",
            "tool_call_id": "cc",
            "llm_response_id": "rr",
        },
    )

    loaded = EventLog(fs)[0]

    assert isinstance(loaded, UnknownEvent)
    assert loaded.id == event_id
    assert loaded.original_kind == "CompletelyRemovedEvent"
    assert loaded.tool_call_id == "cc"
    assert loaded.llm_response_id == "rr"


def test_iter_mixes_good_and_unknown_events_in_order():
    fs = InMemoryFileStore()
    good = _make_action_event("call_good")
    bad = _make_action_event("call_bad")
    bad_payload = json.loads(bad.model_dump_json(exclude_none=True))
    bad_payload["action"]["kind"] = "RemovedFooAction"

    _write(fs, 0, good.id, good.model_dump_json(exclude_none=True))
    _write(fs, 1, bad.id, bad_payload)

    events = list(EventLog(fs))

    assert [type(e) for e in events] == [ActionEvent, UnknownEvent]
    assert events[0].id == good.id
    assert events[1].id == bad.id
    assert isinstance(events[1], UnknownEvent)
    assert events[1].tool_call_id == "call_bad"


def test_malformed_json_still_raises():
    fs = InMemoryFileStore()
    _write(fs, 0, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "{not valid json")

    with pytest.raises(ValueError):
        EventLog(fs)[0]
