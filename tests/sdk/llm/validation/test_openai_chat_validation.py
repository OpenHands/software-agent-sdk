"""Tests for OpenAI Chat Completions API validation schema.

These tests verify that the validation catches common mistakes before
sending requests to the OpenAI API.
"""

import pytest

from openhands.sdk.llm.validation.openai_chat import (
    OpenAIChatCompletionRequest,
    validate_openai_chat_messages,
)


class TestOpenAIChatMessageValidation:
    """Tests for validate_openai_chat_messages function."""

    def test_valid_simple_conversation(self):
        """Test a valid simple conversation."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        errors = validate_openai_chat_messages(messages)
        assert errors == []

    def test_valid_tool_call_flow(self):
        """Test a valid tool_call -> tool message flow."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"location": "NYC"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "content": "Sunny, 72°F",
                "tool_call_id": "call_123",
            },
            {"role": "assistant", "content": "It's sunny and 72°F in NYC."},
        ]
        errors = validate_openai_chat_messages(messages, tools_defined=True)
        assert errors == []

    def test_empty_messages_list(self):
        """Test that empty messages list is rejected."""
        errors = validate_openai_chat_messages([])
        assert len(errors) == 1
        assert "cannot be empty" in errors[0]

    def test_tool_message_must_follow_tool_call(self):
        """Test that tool message must follow assistant with tool_calls."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "tool",
                "content": "Sunny",
                "tool_call_id": "call_orphan",
            },
        ]
        errors = validate_openai_chat_messages(messages, tools_defined=True)
        assert any("does not match any pending tool_call" in e for e in errors)

    def test_tool_message_must_have_matching_id(self):
        """Test that tool message must have matching tool_call_id."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": "{}",
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "content": "Sunny",
                "tool_call_id": "call_WRONG",  # Wrong ID
            },
        ]
        errors = validate_openai_chat_messages(messages, tools_defined=True)
        assert any("does not match any pending tool_call" in e for e in errors)
        assert any("unresolved tool_calls" in e for e in errors)

    def test_duplicate_tool_response_rejected(self):
        """Test that duplicate tool response for same tool_call is rejected."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": "{}",
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "content": "Sunny",
                "tool_call_id": "call_123",
            },
            {
                "role": "tool",
                "content": "Cloudy",  # Duplicate response!
                "tool_call_id": "call_123",
            },
        ]
        errors = validate_openai_chat_messages(messages, tools_defined=True)
        assert any("Duplicate tool response" in e for e in errors)

    def test_tool_calls_require_tools_defined(self):
        """Test that tool_calls require tools to be defined."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": "{}",
                        },
                    },
                ],
            },
        ]
        errors = validate_openai_chat_messages(messages, tools_defined=False)
        assert any("no tools defined" in e for e in errors)

    def test_parallel_tool_calls(self):
        """Test valid parallel tool_calls with multiple tool messages."""
        messages = [
            {"role": "user", "content": "What's the weather in NYC and LA?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_nyc",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"location": "NYC"}',
                        },
                    },
                    {
                        "id": "call_la",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"location": "LA"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "content": "Sunny, 72°F",
                "tool_call_id": "call_nyc",
            },
            {
                "role": "tool",
                "content": "Cloudy, 65°F",
                "tool_call_id": "call_la",
            },
        ]
        errors = validate_openai_chat_messages(messages, tools_defined=True)
        assert errors == []

    def test_unresolved_tool_calls_at_end(self):
        """Test that conversation cannot end with pending tool_calls."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": "{}",
                        },
                    },
                ],
            },
        ]
        errors = validate_openai_chat_messages(messages, tools_defined=True)
        assert any("ends with unresolved tool_calls" in e for e in errors)

    def test_invalid_json_arguments_rejected(self):
        """Test that invalid JSON in function arguments is rejected."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": "not valid json{",  # Invalid JSON
                        },
                    },
                ],
            },
        ]
        errors = validate_openai_chat_messages(messages, tools_defined=True)
        assert any("not valid JSON" in e for e in errors)

    def test_duplicate_tool_call_id_in_same_message(self):
        """Test that duplicate tool_call_id in same message is rejected."""
        messages = [
            {"role": "user", "content": "Get weather twice"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_same",  # Same ID
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": "{}"},
                    },
                    {
                        "id": "call_same",  # Same ID again!
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": "{}"},
                    },
                ],
            },
        ]
        errors = validate_openai_chat_messages(messages, tools_defined=True)
        assert any("Duplicate tool_call id" in e for e in errors)

    def test_new_assistant_with_pending_tool_calls(self):
        """Test that new assistant message with pending tool_calls is an error."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": "{}"},
                    },
                ],
            },
            {"role": "user", "content": "Never mind."},
            {"role": "assistant", "content": "OK."},  # New assistant without tool response
        ]
        errors = validate_openai_chat_messages(messages, tools_defined=True)
        assert any("unresolved tool_calls" in e for e in errors)


class TestOpenAIChatCompletionRequest:
    """Tests for OpenAIChatCompletionRequest pydantic model."""

    def test_valid_request(self):
        """Test creating a valid request."""
        request = OpenAIChatCompletionRequest(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ],
        )
        assert request.model == "gpt-4"
        assert len(request.messages) == 2

    def test_invalid_request_rejected(self):
        """Test that invalid request is rejected."""
        with pytest.raises(ValueError) as exc_info:
            OpenAIChatCompletionRequest(
                model="gpt-4",
                messages=[
                    {"role": "user", "content": "Hello"},
                    {
                        "role": "tool",
                        "content": "Orphan tool message",
                        "tool_call_id": "no_match",
                    },
                ],
            )
        assert "does not match any pending tool_call" in str(exc_info.value)

    def test_temperature_bounds(self):
        """Test temperature parameter bounds."""
        with pytest.raises(ValueError):
            OpenAIChatCompletionRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                temperature=3.0,  # Too high
            )

    def test_max_tokens_must_be_positive(self):
        """Test max_tokens must be positive."""
        with pytest.raises(ValueError):
            OpenAIChatCompletionRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=0,
            )
