"""Tests for Anthropic Messages API validation schema.

These tests verify that the validation catches common mistakes before
sending requests to the Anthropic API.
"""

import pytest

from openhands.sdk.llm.validation.anthropic import (
    AnthropicMessageRequest,
    validate_anthropic_messages,
)


class TestAnthropicMessageValidation:
    """Tests for validate_anthropic_messages function."""

    def test_valid_simple_conversation(self):
        """Test a valid simple user-assistant conversation."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        errors = validate_anthropic_messages(messages)
        assert errors == []

    def test_valid_tool_use_flow(self):
        """Test a valid tool_use -> tool_result flow."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "get_weather",
                        "input": {"location": "NYC"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "Sunny, 72°F",
                    },
                ],
            },
            {"role": "assistant", "content": "It's sunny and 72°F in NYC."},
        ]
        errors = validate_anthropic_messages(messages, tools_defined=True)
        assert errors == []

    def test_empty_messages_list(self):
        """Test that empty messages list is rejected."""
        errors = validate_anthropic_messages([])
        assert len(errors) == 1
        assert "cannot be empty" in errors[0]

    def test_first_message_must_be_user(self):
        """Test that first message must be from user."""
        messages = [
            {"role": "assistant", "content": "Hello"},
        ]
        errors = validate_anthropic_messages(messages)
        assert any("First message must have role 'user'" in e for e in errors)

    def test_roles_must_alternate(self):
        """Test that roles must alternate between user and assistant."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Are you there?"},
        ]
        errors = validate_anthropic_messages(messages)
        assert any("cannot follow itself" in e for e in errors)

    def test_tool_result_must_follow_tool_use(self):
        """Test that tool_result must immediately follow tool_use."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "get_weather",
                        "input": {},
                    },
                ],
            },
            # Missing tool_result - new user message instead
            {"role": "user", "content": "Actually, never mind."},
        ]
        errors = validate_anthropic_messages(messages, tools_defined=True)
        assert any("Missing tool_result" in e for e in errors)

    def test_tool_result_must_have_matching_tool_use_id(self):
        """Test that tool_result must reference a valid tool_use_id."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "get_weather",
                        "input": {},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_WRONG",  # Wrong ID
                        "content": "Sunny",
                    },
                ],
            },
        ]
        errors = validate_anthropic_messages(messages, tools_defined=True)
        # Should have both missing and unexpected errors
        assert any("Missing tool_result" in e for e in errors)
        assert any("Unexpected tool_result" in e for e in errors)

    def test_duplicate_tool_result_rejected(self):
        """Test that duplicate tool_result for same tool_use_id is rejected."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "get_weather",
                        "input": {},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "Sunny",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",  # Duplicate!
                        "content": "Cloudy",
                    },
                ],
            },
        ]
        errors = validate_anthropic_messages(messages, tools_defined=True)
        assert any("Duplicate tool_result" in e for e in errors)

    def test_tool_result_must_come_first_in_content(self):
        """Test that tool_result blocks must come first in user content."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "get_weather",
                        "input": {},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Here's the result:"},  # Text first!
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "Sunny",
                    },
                ],
            },
        ]
        errors = validate_anthropic_messages(messages, tools_defined=True)
        assert any("tool_result blocks must come FIRST" in e for e in errors)

    def test_tool_use_requires_tools_defined(self):
        """Test that tool_use blocks require tools to be defined."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "get_weather",
                        "input": {},
                    },
                ],
            },
        ]
        errors = validate_anthropic_messages(messages, tools_defined=False)
        assert any("tools defined" in e.lower() for e in errors)

    def test_parallel_tool_use(self):
        """Test valid parallel tool_use with multiple tool_results."""
        messages = [
            {"role": "user", "content": "What's the weather in NYC and LA?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_nyc",
                        "name": "get_weather",
                        "input": {"location": "NYC"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_la",
                        "name": "get_weather",
                        "input": {"location": "LA"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_nyc",
                        "content": "Sunny, 72°F",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_la",
                        "content": "Cloudy, 65°F",
                    },
                ],
            },
        ]
        errors = validate_anthropic_messages(messages, tools_defined=True)
        assert errors == []

    def test_unresolved_tool_use_at_end(self):
        """Test that conversation cannot end with pending tool_use."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "get_weather",
                        "input": {},
                    },
                ],
            },
        ]
        errors = validate_anthropic_messages(messages, tools_defined=True)
        assert any("ends with unresolved tool_use" in e for e in errors)

    def test_duplicate_tool_use_id_rejected(self):
        """Test that duplicate tool_use_id is rejected."""
        messages = [
            {"role": "user", "content": "Multiple requests"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_same",
                        "name": "tool_a",
                        "input": {},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_same",
                        "content": "Result 1",
                    },
                ],
            },
            {"role": "assistant", "content": "Got it, let me do another."},
            {
                "role": "user",
                "content": "Sure",
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_same",  # Same ID again!
                        "name": "tool_b",
                        "input": {},
                    },
                ],
            },
        ]
        errors = validate_anthropic_messages(messages, tools_defined=True)
        assert any("Duplicate tool_use id" in e for e in errors)


class TestAnthropicMessageRequest:
    """Tests for AnthropicMessageRequest pydantic model."""

    def test_valid_request(self):
        """Test creating a valid request."""
        request = AnthropicMessageRequest(
            model="claude-3-opus-20240229",
            messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
                {"role": "user", "content": "How are you?"},
            ],
            max_tokens=1024,
        )
        assert request.model == "claude-3-opus-20240229"
        assert len(request.messages) == 3

    def test_invalid_request_rejected(self):
        """Test that invalid request is rejected."""
        with pytest.raises(ValueError) as exc_info:
            AnthropicMessageRequest(
                model="claude-3-opus-20240229",
                messages=[
                    {"role": "assistant", "content": "Hello"},  # Wrong first role
                ],
                max_tokens=1024,
            )
        assert "First message must have role 'user'" in str(exc_info.value)

    def test_tool_use_without_tools_rejected(self):
        """Test that tool_use without tools parameter is rejected."""
        with pytest.raises(ValueError) as exc_info:
            AnthropicMessageRequest(
                model="claude-3-opus-20240229",
                messages=[
                    {"role": "user", "content": "What's the weather?"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_123",
                                "name": "get_weather",
                                "input": {},
                            },
                        ],
                    },
                ],
                max_tokens=1024,
                tools=None,  # No tools defined!
            )
        assert "tools defined" in str(exc_info.value).lower()
