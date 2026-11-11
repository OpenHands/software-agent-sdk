"""Comprehensive tests for event serialization and deserialization."""

import json

import pytest
from pydantic import ValidationError

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


def test_error_origin_fields_round_trip_and_legacy_payloads() -> None:
    """New origin fields survive a JSON round trip; older payloads still load."""
    from openhands.sdk.event.conversation_error import ConversationErrorEvent
    from openhands.sdk.event.error_origin import grouping_key_for

    origin = "openhands.sdk.llm.llm.completion"
    conversation_error = ConversationErrorEvent(
        source="environment",
        code="TypeError",
        detail="secret /tmp/hidden sk-ant-SECRET",
        origin=origin,
    )
    restored = ConversationErrorEvent.model_validate_json(
        conversation_error.model_dump_json()
    )
    assert restored == conversation_error
    assert restored.origin == origin
    assert restored.grouping_key == grouping_key_for(origin, "TypeError")
    assert "secret" not in (restored.origin or "")
    assert "sk-ant-SECRET" not in (restored.grouping_key or "")

    legacy = ConversationErrorEvent.model_validate(
        {"source": "environment", "code": "TypeError", "detail": "legacy"}
    )
    assert legacy.origin is None
    assert legacy.grouping_key is None
    assert legacy.classification is not None
    assert legacy.classification.kind == "internal"

    agent_key = grouping_key_for(origin, "ValueError")
    agent_error = AgentErrorEvent(
        error="secret /tmp/hidden sk-ant-SECRET",
        tool_name="bash",
        tool_call_id="call_001",
        origin=origin,
        grouping_key=agent_key,
    )
    restored_agent = AgentErrorEvent.model_validate_json(agent_error.model_dump_json())
    assert restored_agent.origin == origin
    assert restored_agent.grouping_key == agent_key
    assert restored_agent.classification is not None
    assert restored_agent.classification.kind == "unknown"

    legacy_agent = AgentErrorEvent.model_validate(
        {"error": "legacy", "tool_name": "bash", "tool_call_id": "call_001"}
    )
    assert legacy_agent.origin is None
    assert legacy_agent.grouping_key is None


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
