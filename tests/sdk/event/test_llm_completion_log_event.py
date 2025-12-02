"""Tests for LLMCompletionLogEvent serialization and functionality."""

import json

from openhands.sdk.event import Event, LLMCompletionLogEvent


def test_llm_completion_log_event_creation() -> None:
    """Test creating an LLMCompletionLogEvent."""
    event = LLMCompletionLogEvent(
        filename="test_model__1234567890.123-abcd.json",
        log_data='{"test": "data"}',
        model_name="test_model",
    )

    assert event.filename == "test_model__1234567890.123-abcd.json"
    assert event.log_data == '{"test": "data"}'
    assert event.model_name == "test_model"
    assert event.source == "environment"


def test_llm_completion_log_event_serialization() -> None:
    """Test LLMCompletionLogEvent serialization/deserialization."""
    log_data = json.dumps(
        {
            "response": {"id": "response_123", "model": "test_model"},
            "cost": 0.0001,
            "timestamp": 1234567890.123,
        }
    )

    event = LLMCompletionLogEvent(
        filename="anthropic__claude-sonnet__1234567890.123-abcd.json",
        log_data=log_data,
        model_name="anthropic/claude-sonnet",
    )

    # Serialize
    json_str = event.model_dump_json()
    deserialized = LLMCompletionLogEvent.model_validate_json(json_str)

    assert deserialized == event
    assert deserialized.filename == event.filename
    assert deserialized.log_data == event.log_data
    assert deserialized.model_name == event.model_name


def test_llm_completion_log_event_as_base_event() -> None:
    """Test that LLMCompletionLogEvent can be deserialized as base Event."""
    event = LLMCompletionLogEvent(
        filename="test_model__1234567890.123-abcd.json",
        log_data='{"test": "data"}',
        model_name="test_model",
    )

    # Serialize and deserialize as base Event
    json_str = event.model_dump_json()
    deserialized = Event.model_validate_json(json_str)

    assert isinstance(deserialized, LLMCompletionLogEvent)
    assert deserialized == event


def test_llm_completion_log_event_str() -> None:
    """Test string representation of LLMCompletionLogEvent."""
    event = LLMCompletionLogEvent(
        filename="test_model__1234567890.123-abcd.json",
        log_data='{"test": "data"}',
        model_name="test_model",
    )

    str_repr = str(event)
    assert "test_model" in str_repr
    assert "test_model__1234567890.123-abcd.json" in str_repr
