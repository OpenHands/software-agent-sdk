"""Tests for LLM input validation and SDK corruption bugs.

Two types of tests:
1. XFAIL tests demonstrating SDK bugs (will pass when bugs are fixed)
2. Passing tests showing validation catches these issues before API calls
"""

import json

import pytest

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.exceptions import LLMInputValidationError
from openhands.sdk.llm.validation import (
    AnthropicMessageValidator,
    OpenAIChatMessageValidator,
    OpenAIResponsesInputValidator,
    get_validator,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


# ============================================================================
# XFAIL tests - These demonstrate SDK bugs that still exist
# When fixed, these tests will start passing
# ============================================================================


class TestSDKBugsStillExist:
    """XFAIL tests for SDK bugs. Will pass when bugs are fixed."""

    @pytest.mark.xfail(reason="Bug #1782: events_to_messages doesn't deduplicate", strict=True)
    def test_duplicate_observations_not_filtered(self):
        """SDK produces duplicate tool_results for same tool_call_id."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="x",
            tool_call_id="tc1",
            tool_call=MessageToolCall(id="tc1", name="x", arguments="{}", origin="completion"),
            llm_response_id="r1",
            source="agent",
        )
        obs1 = ObservationEvent(
            id="o1",
            observation=MCPToolObservation.from_text("a", "x"),
            tool_name="x",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )
        obs2 = ObservationEvent(
            id="o2",
            observation=MCPToolObservation.from_text("b", "x"),
            tool_name="x",
            tool_call_id="tc1",  # duplicate
            action_id="a1",
            source="environment",
        )

        messages = LLMConvertibleEvent.events_to_messages([action, obs1, obs2])
        tool_results = [m for m in messages if m.role == "tool"]
        assert len(tool_results) == 1, f"Expected 1, got {len(tool_results)}"

    @pytest.mark.xfail(reason="Bug #2127: events_to_messages includes orphan tool_use", strict=True)
    def test_orphan_action_not_filtered(self):
        """SDK includes tool_use without matching tool_result."""
        user = MessageEvent(
            id="m1",
            llm_message=Message(role="user", content=[TextContent(text="hi")]),
            source="user",
        )
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="x",
            tool_call_id="tc1",
            tool_call=MessageToolCall(id="tc1", name="x", arguments="{}", origin="completion"),
            llm_response_id="r1",
            source="agent",
        )
        # NO observation - simulates crash

        messages = LLMConvertibleEvent.events_to_messages([user, action])
        assistant_with_tools = [m for m in messages if m.role == "assistant" and m.tool_calls]
        tool_results = [m for m in messages if m.role == "tool"]

        for msg in assistant_with_tools:
            for tc in msg.tool_calls or []:
                assert any(r.tool_call_id == tc.id for r in tool_results), f"Orphan {tc.id}"


class TestValidatorFactory:
    """Test get_validator() returns correct validator by model and response_type."""

    def test_anthropic_completion(self):
        v = get_validator("claude-3-opus", response_type="completion")
        assert isinstance(v, AnthropicMessageValidator)

    def test_openai_completion(self):
        v = get_validator("gpt-4o", response_type="completion")
        assert isinstance(v, OpenAIChatMessageValidator)

    def test_responses_api(self):
        v = get_validator("gpt-4o", response_type="responses")
        assert isinstance(v, OpenAIResponsesInputValidator)


class TestAnthropicValidation:
    """Key Anthropic-specific validation rules."""

    def test_catches_duplicate_tool_result(self):
        """Issue #1782: Duplicate tool_result for same tool_use_id."""
        messages = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "a"},
                {"type": "tool_result", "tool_use_id": "t1", "content": "b"},  # dup
            ]},
        ]
        errors = AnthropicMessageValidator().validate(messages, tools_defined=True)
        assert any("Duplicate" in e for e in errors)

    def test_catches_missing_tool_result(self):
        """Issue #2127: tool_use without tool_result."""
        messages = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
            ]},
            {"role": "user", "content": "continue"},  # no tool_result
        ]
        errors = AnthropicMessageValidator().validate(messages, tools_defined=True)
        assert any("tool_result" in e.lower() or "unresolved" in e.lower() for e in errors)


class TestOpenAIChatValidation:
    """Key OpenAI Chat validation rules."""

    def test_catches_duplicate_tool_response(self):
        """Issue #1782: Duplicate tool response."""
        messages = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "a"},
            {"role": "tool", "tool_call_id": "c1", "content": "b"},  # dup
        ]
        errors = OpenAIChatMessageValidator().validate(messages, tools_defined=True)
        assert any("Duplicate" in e for e in errors)

    def test_catches_orphan_tool_call(self):
        """Issue #2127: tool_call without response."""
        messages = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "user", "content": "continue"},  # no tool response
        ]
        errors = OpenAIChatMessageValidator().validate(messages, tools_defined=True)
        assert any("unresolved" in e.lower() for e in errors)


class TestResponsesValidation:
    """Key Responses API validation rules."""

    def test_catches_duplicate_function_output(self):
        """Duplicate function_call_output for same call_id."""
        input_items = [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "test"}]},
            {"type": "function_call", "call_id": "fc1", "name": "x", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "fc1", "output": "a"},
            {"type": "function_call_output", "call_id": "fc1", "output": "b"},  # dup
        ]
        errors = OpenAIResponsesInputValidator().validate(input_items, tools_defined=True)
        assert any("Duplicate" in e for e in errors)


class TestValidateOrRaise:
    """Test validate_or_raise raises LLMInputValidationError."""

    def test_raises_with_details(self):
        messages = [
            {"role": "tool", "tool_call_id": "orphan", "content": "x"},
        ]
        with pytest.raises(LLMInputValidationError) as exc:
            OpenAIChatMessageValidator().validate_or_raise(messages, tools_defined=True)
        assert exc.value.provider == "openai_chat"
        assert len(exc.value.errors) > 0
