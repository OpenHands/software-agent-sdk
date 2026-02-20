"""Tests for event stream validation/repair and LLM input validation.

Tests verify that:
1. Event stream repair fixes corrupt states (orphan actions, duplicates)
2. LLM message validators catch issues as a safety net
"""

import pytest

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.event.validation import (
    repair_event_stream,
    validate_event_stream,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.exceptions import LLMInputValidationError
from openhands.sdk.llm.validation import (
    AnthropicMessageValidator,
    OpenAIChatMessageValidator,
    OpenAIResponsesInputValidator,
    get_validator,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


class TestEventStreamValidation:
    """Tests for validate_event_stream()."""

    def test_valid_stream(self):
        """Valid event stream returns no errors."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="x",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="x", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )
        obs = ObservationEvent(
            id="o1",
            observation=MCPToolObservation.from_text("result", tool_name="x"),
            tool_name="x",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )
        errors = validate_event_stream([action, obs])
        assert errors == []

    def test_detects_orphan_action(self):
        """Detects action without observation."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="x",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="x", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )
        errors = validate_event_stream([action])
        assert len(errors) == 1
        assert "Orphan action" in errors[0]


class TestEventStreamRepair:
    """Tests for repair_event_stream()."""

    def test_adds_synthetic_observation_for_orphan_action(self):
        """Adds synthetic error observation for orphan action."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="terminal",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="terminal", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )

        repaired, repairs = repair_event_stream([action])

        assert len(repairs) == 1
        assert "synthetic" in repairs[0].lower()
        assert len(repaired) == 2

        errors = validate_event_stream(repaired)
        assert errors == []

    def test_removes_duplicate_observations(self):
        """Removes duplicate observations, keeps first."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="x",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="x", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )
        obs1 = ObservationEvent(
            id="o1",
            observation=MCPToolObservation.from_text("first", tool_name="x"),
            tool_name="x",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )
        obs2 = ObservationEvent(
            id="o2",
            observation=MCPToolObservation.from_text("dup", tool_name="x"),
            tool_name="x",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )

        repaired, repairs = repair_event_stream([action, obs1, obs2])

        assert len(repairs) == 1
        assert "duplicate" in repairs[0].lower()
        assert len(repaired) == 2


class TestEventsToMessagesWithRepair:
    """Tests that events_to_messages() repairs corrupt streams."""

    def test_repairs_orphan_action(self):
        """Orphan action gets synthetic observation."""
        user_msg = MessageEvent(
            id="m1",
            llm_message=Message(role="user", content=[TextContent(text="hi")]),
            source="user",
        )
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="terminal",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="terminal", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )

        messages = LLMConvertibleEvent.events_to_messages([user_msg, action])
        tool_results = [m for m in messages if m.role == "tool"]
        assert len(tool_results) == 1

    def test_repairs_duplicate_observations(self):
        """Duplicate observations are deduplicated."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="terminal",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="terminal", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )
        obs1 = ObservationEvent(
            id="o1",
            observation=MCPToolObservation.from_text("first", tool_name="terminal"),
            tool_name="terminal",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )
        obs2 = ObservationEvent(
            id="o2",
            observation=MCPToolObservation.from_text("dup", tool_name="terminal"),
            tool_name="terminal",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )

        messages = LLMConvertibleEvent.events_to_messages([action, obs1, obs2])
        tool_results = [m for m in messages if m.role == "tool"]
        assert len(tool_results) == 1


class TestValidatorFactory:
    """Test get_validator() returns correct validator."""

    def test_anthropic_completion(self):
        v = get_validator("claude-3-opus", response_type="completion")
        assert isinstance(v, AnthropicMessageValidator)

    def test_openai_completion(self):
        v = get_validator("gpt-4o", response_type="completion")
        assert isinstance(v, OpenAIChatMessageValidator)

    def test_responses_api(self):
        v = get_validator("gpt-4o", response_type="responses")
        assert isinstance(v, OpenAIResponsesInputValidator)


class TestValidateOrRaise:
    """Test validate_or_raise raises LLMInputValidationError."""

    def test_raises_with_details(self):
        messages = [{"role": "tool", "tool_call_id": "orphan", "content": "x"}]
        with pytest.raises(LLMInputValidationError) as exc:
            OpenAIChatMessageValidator().validate_or_raise(messages, tools_defined=True)
        assert exc.value.provider == "openai_chat"
        assert len(exc.value.errors) > 0
