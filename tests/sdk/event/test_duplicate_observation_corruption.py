"""Tests for LLM input validation catching conversation corruption issues.

These tests verify that the pre-flight validation system catches the specific
corruption scenarios that caused production issues:

- Issue #1782: Duplicate ObservationEvent with same tool_call_id
- Issue #2127: Missing tool_result block for tool_use  
- Issue #1841: send_message() during pending tool execution corrupts ordering

The validation catches these BEFORE sending to the API, avoiding the errors:
- "unexpected `tool_use_id` found in `tool_result` blocks"
- "`tool_use` ids were found without `tool_result` blocks immediately after"
"""

import pytest

from openhands.sdk.llm.exceptions import LLMInputValidationError
from openhands.sdk.llm.validation import (
    AnthropicMessageValidator,
    OpenAIChatMessageValidator,
    get_chat_validator,
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
