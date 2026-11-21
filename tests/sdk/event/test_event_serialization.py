"""Comprehensive tests for event serialization and deserialization."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Condensation,
    CondensationRequest,
    Event,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.event.error_classification import FailureKind
from openhands.sdk.io.local import LocalFileStore
from openhands.sdk.llm import (
    Message,
    MessageToolCall,
    TextContent,
)
from openhands.sdk.tool import Action, Observation


class EventSerializationMockEvent(Event):
    test_field: str = "test_value"


class EventsSerializationMockAction(Action):
    """Mock action for testing."""

    def execute(self) -> "EventsSerializationMockObservation":
        return EventsSerializationMockObservation(
            content=[TextContent(text="mock result")]
        )


class EventsSerializationMockObservation(Observation):
    """Mock observation for testing."""

    pass


def test_event_base_serialization() -> None:
    """Test basic Event serialization/deserialization."""
    event = EventSerializationMockEvent(source="agent", test_field="custom_value")

    json_data = event.model_dump_json()
    deserialized = EventSerializationMockEvent.model_validate_json(json_data)
    assert deserialized == event


def test_system_prompt_event_serialization() -> None:
    """Test SystemPromptEvent serialization/deserialization."""
    event = SystemPromptEvent(
        system_prompt=TextContent(text="You are a helpful assistant"), tools=[]
    )

    json_data = event.model_dump_json()
    deserialized = SystemPromptEvent.model_validate_json(json_data)
    assert deserialized == event


def test_action_event_serialization() -> None:
    """Test ActionEvent serialization/deserialization."""
    action = EventsSerializationMockAction()
    tool_call = MessageToolCall(
        id="call_123",
        name="mock_tool",
        arguments="{}",
        origin="completion",
    )
    event = ActionEvent(
        thought=[TextContent(text="I need to do something")],
        action=action,
        tool_name="mock_tool",
        tool_call_id="call_123",
        tool_call=tool_call,
        llm_response_id="response_456",
    )

    json_data = event.model_dump_json()
    deserialized = ActionEvent.model_validate_json(json_data)

    # Check that the core fields are preserved
    assert deserialized.id == event.id
    assert deserialized.timestamp == event.timestamp
    assert deserialized.source == event.source
    assert deserialized.thought == event.thought
    assert deserialized.tool_name == event.tool_name
    assert deserialized.tool_call_id == event.tool_call_id
    assert deserialized.tool_call == event.tool_call
    assert deserialized.llm_response_id == event.llm_response_id
    # Action is deserialized as Action, so we can't check exact equality


def test_observation_event_serialization() -> None:
    """Test ObservationEvent serialization/deserialization."""
    observation = EventsSerializationMockObservation(
        content=[TextContent(text="test result")]
    )
    event = ObservationEvent(
        observation=observation,
        action_id="action_123",
        tool_name="mock_tool",
        tool_call_id="call_123",
    )

    json_data = event.model_dump_json()
    deserialized = ObservationEvent.model_validate_json(json_data)

    # Check that the core fields are preserved
    assert deserialized.id == event.id
    assert deserialized.timestamp == event.timestamp
    assert deserialized.source == event.source
    assert deserialized.action_id == event.action_id
    assert deserialized.tool_name == event.tool_name
    assert deserialized.tool_call_id == event.tool_call_id
    # Observation is deserialized as Observation, so we can't check exact equality


def test_message_event_serialization() -> None:
    """Test MessageEvent serialization/deserialization."""
    from openhands.sdk.llm import Message

    llm_message = Message(
        role="user",
        content=[TextContent(text="Hello, world!")],
    )
    event = MessageEvent(source="user", llm_message=llm_message)

    json_data = event.model_dump_json()
    deserialized = MessageEvent.model_validate_json(json_data)
    assert deserialized == event


def test_agent_error_event_serialization() -> None:
    """Test AgentErrorEvent serialization/deserialization."""
    event = AgentErrorEvent(
        error="Something went wrong", tool_call_id="call_001", tool_name="test_tool"
    )

    json_data = event.model_dump_json()
    deserialized = AgentErrorEvent.model_validate_json(json_data)
    assert deserialized == event


@pytest.mark.parametrize("recovery_reason", [None, "server_restart"])
def test_agent_error_provenance_survives_event_log_round_trip(
    tmp_path: Path, recovery_reason: str | None
) -> None:
    payload = {
        "kind": "AgentErrorEvent",
        "source": "agent",
        "error": "Interrupted execution",
        "tool_call_id": "call_001",
        "tool_name": "test_tool",
        "classification": {"kind": "internal", "retryable": False},
    }
    if recovery_reason is not None:
        payload["recovery_reason"] = recovery_reason
    event = Event.model_validate_json(json.dumps(payload))
    assert isinstance(event, AgentErrorEvent)
    assert event.recovery_reason == recovery_reason

    store = LocalFileStore(str(tmp_path))
    log = EventLog(store)
    log.append(event)
    del log
    restored = EventLog(store)[0]

    assert isinstance(restored, AgentErrorEvent)
    assert restored == event
    assert restored.recovery_reason == recovery_reason
    assert restored.classification is not None
    assert restored.classification.kind == FailureKind.INTERNAL
    assert restored.classification.retryable is False


def test_condensation_serialization() -> None:
    """Test Condensation serialization/deserialization."""
    event = Condensation(
        summary="This is a summary",
        forgotten_event_ids={"event1", "event2", "event3", "event4", "event5"},
        llm_response_id="condensation_response_1",
    )

    # Serialize
    json_data = event.model_dump_json()
    deserialized = Condensation.model_validate_json(json_data)
    assert deserialized == event


def test_condensation_deserializes_from_list_format() -> None:
    """Backward compat: old persisted data stored forgotten_event_ids as a list."""
    event = Condensation(
        summary="summary",
        forgotten_event_ids={"id1", "id2"},
        llm_response_id="resp_1",
    )
    raw = json.loads(event.model_dump_json())

    # Simulate the old persisted format: a JSON array (list) of IDs
    raw["forgotten_event_ids"] = ["id1", "id2"]
    deserialized = Condensation.model_validate(raw)

    assert deserialized.forgotten_event_ids == {"id1", "id2"}
    assert isinstance(deserialized.forgotten_event_ids, set)


def test_condensation_request_serialization() -> None:
    """Test CondensationRequest serialization/deserialization."""
    event = CondensationRequest()

    json_data = event.model_dump_json()
    deserialized = CondensationRequest.model_validate_json(json_data)
    assert deserialized == event


def test_extra_fields_forbidden():
    """Test that extra fields are forbidden in events."""
    data_with_extra = {
        "type": "SystemPromptEvent",
        "source": "agent",
        "id": "test-id",
        "timestamp": "2023-01-01T00:00:00",
        "system_prompt": {"text": "Test"},
        "tools": [],
        "extra_field": "should_not_be_allowed",
    }

    with pytest.raises(ValidationError) as exc_info:
        SystemPromptEvent.model_validate(data_with_extra)

    assert "extra_forbidden" in str(exc_info.value)


def test_event_deserialize():
    original = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text="Hello There!")],
        ),
        activated_skills=[],
        extended_content=[],
    )
    dumped = original.model_dump_json()
    loaded = Event.model_validate_json(dumped)
    assert loaded == original


def test_condensation_legacy_and_enriched_serialization_compat() -> None:
    """Legacy Condensation & CondensationSummaryEvent payloads without size/span
    fields still deserialize and apply to View cleanly; new fields round-trip.
    """
    from openhands.sdk.context.view import View
    from openhands.sdk.event.condenser import Condensation, CondensationSummaryEvent

    m0 = MessageEvent(
        id="evt-0",
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="hello")]),
    )
    m1 = MessageEvent(
        id="evt-1",
        source="agent",
        llm_message=Message(role="assistant", content=[TextContent(text="world")]),
    )

    legacy_condensation_json = {
        "id": "cond-legacy-1",
        "timestamp": "2026-01-01T00:00:00",
        "source": "environment",
        "kind": "Condensation",
        "forgotten_event_ids": ["evt-0"],
        "summary": "Legacy summary",
        "summary_offset": 0,
        "llm_response_id": "resp-legacy",
    }
    legacy_cond = Event.model_validate(legacy_condensation_json)
    assert isinstance(legacy_cond, Condensation)
    assert legacy_cond.chars_before is None
    assert legacy_cond.chars_after is None
    assert legacy_cond.tokens_before is None
    assert legacy_cond.tokens_after is None

    view = View.from_events([m0, m1, legacy_cond])
    assert len(view) == 2
    assert isinstance(view.events[0], CondensationSummaryEvent)
    assert view.events[0].summary == "Legacy summary"

    legacy_summary_json = {
        "id": "sum-legacy-1",
        "timestamp": "2026-01-01T00:00:00",
        "source": "environment",
        "kind": "CondensationSummaryEvent",
        "summary": "Standalone legacy summary",
    }
    legacy_sum = Event.model_validate(legacy_summary_json)
    assert isinstance(legacy_sum, CondensationSummaryEvent)
    assert legacy_sum.forgotten_event_count is None
    assert legacy_sum.summary_offset is None
    assert "Standalone legacy summary" in legacy_sum.to_llm_message().content[0].text  # type: ignore[union-attr]

    enriched = Condensation(
        forgotten_event_ids={"evt-0"},
        summary="Enriched summary",
        summary_offset=0,
        llm_response_id="resp-2",
        chars_before=240,
        chars_after=16,
        tokens_before=60,
        tokens_after=12,
    )
    restored = Event.model_validate_json(enriched.model_dump_json())
    assert isinstance(restored, Condensation)
    assert restored.chars_before == 240
    assert restored.chars_after == 16
    assert restored.tokens_before == 60
    assert restored.tokens_after == 12


def test_condensation_size_fields_appear_in_agent_server_openapi() -> None:
    """New optional Condensation fields are exposed in agent-server OpenAPI schema."""
    from openhands.agent_server.api import create_app
    from openhands.agent_server.config import Config

    app = create_app(Config(session_api_keys=["test-key"], enable_vscode=False))
    schema = app.openapi()
    condensation_props = schema["components"]["schemas"]["Condensation"]["properties"]
    for field_name in ("chars_before", "chars_after", "tokens_before", "tokens_after"):
        assert field_name in condensation_props


def test_condensation_summary_str_preserves_full_summary_beyond_preview_limit() -> None:
    """str(CondensationSummaryEvent) keeps the entire summary (>500 chars) so
    subsequent rolling condensations do not chop off <preserved_facts>.
    """
    from openhands.sdk.event.condenser import CondensationSummaryEvent

    long_summary = (
        "USER_CONTEXT: Long task\n"
        + ("COMPLETED: Step details. " * 30)
        + "\n<preserved_facts>\n- cert_sha256: 9F:86:D0:81:88:4C:7D:65\n"
        + "</preserved_facts>"
    )
    assert len(long_summary) > 700
    evt = CondensationSummaryEvent(summary=long_summary)
    rendered = str(evt)
    assert "9F:86:D0:81:88:4C:7D:65" in rendered
    assert "</preserved_facts>" in rendered
