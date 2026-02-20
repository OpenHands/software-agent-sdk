"""Tests for conversation corruption issues and validation.

Two types of tests:
1. XFAIL tests that demonstrate bugs still exist in the SDK
2. PASSING tests that show the new validation catches these issues

Issues covered:
- Issue #1782: Duplicate ObservationEvent with same tool_call_id
- Issue #2127: Missing tool_result block for tool_use  
- Issue #1841: send_message() during pending tool execution corrupts ordering
"""

import json

import pytest

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.exceptions import LLMInputValidationError
from openhands.sdk.llm.validation import (
    AnthropicMessageValidator,
    OpenAIChatMessageValidator,
    get_chat_validator,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


# ============================================================================
# Helper functions for creating events
# ============================================================================


def create_action_event(
    event_id: str,
    tool_call_id: str,
    tool_name: str = "terminal",
) -> ActionEvent:
    """Helper to create an ActionEvent."""
    action = MCPToolAction(data={"command": "ls"})
    tool_call = MessageToolCall(
        id=tool_call_id,
        name=tool_name,
        arguments=json.dumps({"command": "ls"}),
        origin="completion",
    )
    return ActionEvent(
        id=event_id,
        thought=[TextContent(text="Running command")],
        action=action,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id="resp_1",
        source="agent",
    )


def create_observation_event(
    event_id: str,
    action_id: str,
    tool_call_id: str,
    result: str = "output",
) -> ObservationEvent:
    """Helper to create an ObservationEvent."""
    observation = MCPToolObservation.from_text(text=result, tool_name="terminal")
    return ObservationEvent(
        id=event_id,
        observation=observation,
        tool_name="terminal",
        tool_call_id=tool_call_id,
        action_id=action_id,
        source="environment",
    )


def create_message_event(event_id: str, content: str) -> MessageEvent:
    """Helper to create a MessageEvent."""
    return MessageEvent(
        id=event_id,
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


# ============================================================================
# XFAIL tests - These demonstrate bugs that still exist in the SDK
# ============================================================================


class TestSDKBugsStillExist:
    """XFAIL tests demonstrating that the underlying SDK bugs still exist.
    
    These tests will FAIL (marked xfail) because the SDK doesn't handle these
    corrupt states. The validation layer catches them before they reach the API,
    but the bugs in events_to_messages() etc. are still present.
    """

    @pytest.mark.xfail(
        reason="Bug #1782: events_to_messages() doesn't deduplicate observations",
        strict=True,
    )
    def test_duplicate_observations_not_filtered_by_sdk(self):
        """SDK doesn't filter duplicate observations with same tool_call_id."""
        user_msg = create_message_event("msg_1", "List files")
        action = create_action_event("action_1", "toolu_dup")
        obs_1 = create_observation_event("obs_1", "action_1", "toolu_dup", "file1.txt")
        obs_2 = create_observation_event("obs_2", "action_1", "toolu_dup", "duplicate!")

        events: list[LLMConvertibleEvent] = [user_msg, action, obs_1, obs_2]
        messages = LLMConvertibleEvent.events_to_messages(events)

        tool_results = [m for m in messages if m.role == "tool"]
        
        # BUG: SDK produces 2 tool_results for same tool_call_id
        # This would cause Anthropic API error: "unexpected tool_use_id"
        assert len(tool_results) == 1, (
            f"Expected 1 tool_result, got {len(tool_results)}. "
            "SDK should deduplicate observations by tool_call_id."
        )

    @pytest.mark.xfail(
        reason="Bug #2127: events_to_messages() includes orphan tool_use",
        strict=True,
    )
    def test_orphan_action_not_filtered_by_sdk(self):
        """SDK doesn't filter actions without matching observations."""
        user_msg_1 = create_message_event("msg_1", "Run command")
        action = create_action_event("action_orphan", "toolu_orphan")
        # NO observation - simulating crash
        user_msg_2 = create_message_event("msg_2", "What happened?")

        events: list[LLMConvertibleEvent] = [user_msg_1, action, user_msg_2]
        messages = LLMConvertibleEvent.events_to_messages(events)

        # Check for orphan tool_use without tool_result
        assistant_with_tools = [m for m in messages if m.role == "assistant" and m.tool_calls]
        tool_results = [m for m in messages if m.role == "tool"]

        for msg in assistant_with_tools:
            for tc in msg.tool_calls or []:
                has_result = any(r.tool_call_id == tc.id for r in tool_results)
                # BUG: SDK includes tool_use without matching tool_result
                # This would cause API error: "tool_use ids without tool_result"
                assert has_result, (
                    f"tool_use {tc.id} has no matching tool_result. "
                    "SDK should filter orphan actions."
                )


class TestValidationCatchesDuplicateToolResults:
    """Tests for Issue #1782: Duplicate tool_result for same tool_call_id.

    This occurs when a conversation is resumed and an action is re-executed,
    creating a duplicate observation/tool_result.
    """

    def test_anthropic_catches_duplicate_tool_result(self):
        """Anthropic validator catches duplicate tool_result for same tool_use_id."""
        messages = [
            {"role": "user", "content": "List the files"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "terminal",
                        "input": {"command": "ls"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "file1.txt",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",  # DUPLICATE
                        "content": "file1.txt (re-executed)",
                    },
                ],
            },
        ]

        validator = AnthropicMessageValidator()
        errors = validator.validate(messages, tools_defined=True)

        assert len(errors) > 0
        assert any("Duplicate tool_result" in e for e in errors)

    def test_openai_catches_duplicate_tool_response(self):
        """OpenAI validator catches duplicate tool response for same tool_call_id."""
        messages = [
            {"role": "user", "content": "List the files"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "content": "file1.txt", "tool_call_id": "call_123"},
            {
                "role": "tool",
                "content": "file1.txt (duplicate)",
                "tool_call_id": "call_123",  # DUPLICATE
            },
        ]

        validator = OpenAIChatMessageValidator()
        errors = validator.validate(messages, tools_defined=True)

        assert len(errors) > 0
        assert any("Duplicate tool response" in e for e in errors)


class TestValidationCatchesMissingToolResult:
    """Tests for Issue #2127: Missing tool_result for tool_use.

    This occurs when a pod crashes mid-execution and the observation
    is never persisted, leaving an orphan tool_use.
    """

    def test_anthropic_catches_missing_tool_result(self):
        """Anthropic validator catches tool_use without tool_result."""
        messages = [
            {"role": "user", "content": "Run a command"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_orphan",
                        "name": "terminal",
                        "input": {"command": "ls"},
                    },
                ],
            },
            # NO tool_result - simulating crash before completion
            {"role": "user", "content": "What happened?"},
        ]

        validator = AnthropicMessageValidator()
        errors = validator.validate(messages, tools_defined=True)

        assert len(errors) > 0
        assert any("Missing tool_result" in e or "unresolved tool_use" in e for e in errors)

    def test_openai_catches_missing_tool_response(self):
        """OpenAI validator catches tool_call without tool response."""
        messages = [
            {"role": "user", "content": "Run a command"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_orphan",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": "{}"},
                    },
                ],
            },
            # NO tool response - simulating crash
            {"role": "user", "content": "What happened?"},
            {"role": "assistant", "content": "Let me check..."},
        ]

        validator = OpenAIChatMessageValidator()
        errors = validator.validate(messages, tools_defined=True)

        assert len(errors) > 0
        assert any("unresolved tool_calls" in e for e in errors)


class TestValidationCatchesMessageOrderingIssues:
    """Tests for Issue #1841: Messages between tool_use and tool_result.

    This occurs when send_message() is called during tool execution,
    inserting a user message between tool_use and tool_result.
    """

    def test_anthropic_catches_wrong_message_order(self):
        """Anthropic validator catches user message between tool_use and tool_result."""
        messages = [
            {"role": "user", "content": "Run a command"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_mid",
                        "name": "terminal",
                        "input": {},
                    },
                ],
            },
            # User message BEFORE tool_result - this is the bug
            {"role": "user", "content": "Are you still working?"},
        ]

        validator = AnthropicMessageValidator()
        errors = validator.validate(messages, tools_defined=True)

        # Anthropic requires tool_result immediately after tool_use
        assert len(errors) > 0
        assert any(
            "Missing tool_result" in e or "unresolved tool_use" in e for e in errors
        )

    def test_anthropic_requires_tool_result_first_in_content(self):
        """Anthropic validator catches text before tool_result in user content."""
        messages = [
            {"role": "user", "content": "Run a command"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_123", "name": "terminal", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Here's the result:"},  # TEXT FIRST - error!
                    {"type": "tool_result", "tool_use_id": "toolu_123", "content": "done"},
                ],
            },
        ]

        validator = AnthropicMessageValidator()
        errors = validator.validate(messages, tools_defined=True)

        assert len(errors) > 0
        assert any("tool_result blocks must come FIRST" in e for e in errors)


class TestValidatorSelectionByModel:
    """Test that the correct validator is selected based on model name."""

    def test_anthropic_model_gets_anthropic_validator(self):
        """Anthropic models get the stricter Anthropic validator."""
        for model in ["claude-3-opus", "anthropic/claude-3-sonnet", "Claude-3-Haiku"]:
            validator = get_chat_validator(model)
            assert isinstance(validator, AnthropicMessageValidator), f"Wrong validator for {model}"

    def test_openai_model_gets_openai_validator(self):
        """OpenAI models get the OpenAI validator."""
        for model in ["gpt-4", "gpt-4o", "openai/gpt-4-turbo"]:
            validator = get_chat_validator(model)
            assert isinstance(validator, OpenAIChatMessageValidator), f"Wrong validator for {model}"


class TestValidateOrRaiseIntegration:
    """Test the validate_or_raise method raises LLMInputValidationError."""

    def test_raises_with_error_details(self):
        """validate_or_raise raises LLMInputValidationError with details."""
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "tool",
                "content": "Orphan",
                "tool_call_id": "no_match",
            },
        ]

        validator = OpenAIChatMessageValidator()

        with pytest.raises(LLMInputValidationError) as exc_info:
            validator.validate_or_raise(messages, tools_defined=True)

        assert exc_info.value.provider == "openai_chat"
        assert len(exc_info.value.errors) > 0
        assert "no_match" in str(exc_info.value)
