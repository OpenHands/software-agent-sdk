"""Tests for OpenAI Responses API validation schema.

These tests verify that the validation catches common mistakes before
sending requests to the OpenAI Responses API.
"""

import pytest

from openhands.sdk.llm.validation.openai_responses import (
    OpenAIResponsesRequest,
    validate_openai_responses_input,
)


class TestOpenAIResponsesInputValidation:
    """Tests for validate_openai_responses_input function."""

    def test_valid_simple_input(self):
        """Test a valid simple input."""
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": "Hello",
            },
        ]
        errors = validate_openai_responses_input(input_items)
        assert errors == []

    def test_valid_function_call_flow(self):
        """Test a valid function_call -> function_call_output flow."""
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": "What's the weather?",
            },
            {
                "type": "function_call",
                "call_id": "fc_123",
                "name": "get_weather",
                "arguments": '{"location": "NYC"}',
            },
            {
                "type": "function_call_output",
                "call_id": "fc_123",
                "output": "Sunny, 72°F",
            },
        ]
        errors = validate_openai_responses_input(input_items, tools_defined=True)
        assert errors == []

    def test_empty_input_list(self):
        """Test that empty input list is rejected."""
        errors = validate_openai_responses_input([])
        assert len(errors) == 1
        assert "cannot be empty" in errors[0]

    def test_function_call_output_must_have_matching_call_id(self):
        """Test that function_call_output must have matching call_id."""
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": "What's the weather?",
            },
            {
                "type": "function_call",
                "call_id": "fc_123",
                "name": "get_weather",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "fc_WRONG",  # Wrong ID
                "output": "Sunny",
            },
        ]
        errors = validate_openai_responses_input(input_items, tools_defined=True)
        assert any("does not match any preceding function_call" in e for e in errors)

    def test_duplicate_function_call_output_rejected(self):
        """Test that duplicate function_call_output is rejected."""
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": "What's the weather?",
            },
            {
                "type": "function_call",
                "call_id": "fc_123",
                "name": "get_weather",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "fc_123",
                "output": "Sunny",
            },
            {
                "type": "function_call_output",
                "call_id": "fc_123",  # Duplicate!
                "output": "Cloudy",
            },
        ]
        errors = validate_openai_responses_input(input_items, tools_defined=True)
        assert any("Duplicate function_call_output" in e for e in errors)

    def test_function_call_requires_tools_defined(self):
        """Test that function_call requires tools to be defined."""
        input_items = [
            {
                "type": "function_call",
                "call_id": "fc_123",
                "name": "get_weather",
                "arguments": "{}",
            },
        ]
        errors = validate_openai_responses_input(input_items, tools_defined=False)
        assert any("no tools defined" in e for e in errors)

    def test_invalid_json_arguments_rejected(self):
        """Test that invalid JSON in function arguments is rejected."""
        input_items = [
            {
                "type": "function_call",
                "call_id": "fc_123",
                "name": "get_weather",
                "arguments": "not valid json{",  # Invalid JSON
            },
        ]
        errors = validate_openai_responses_input(input_items, tools_defined=True)
        assert any("not valid JSON" in e for e in errors)

    def test_orphan_function_call_output(self):
        """Test that orphan function_call_output is rejected."""
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": "Hello",
            },
            {
                "type": "function_call_output",
                "call_id": "fc_orphan",
                "output": "Orphan output",
            },
        ]
        errors = validate_openai_responses_input(input_items, tools_defined=True)
        assert any("does not match any preceding function_call" in e for e in errors)

    def test_parallel_function_calls(self):
        """Test valid parallel function_calls with multiple outputs."""
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": "Get weather for NYC and LA",
            },
            {
                "type": "function_call",
                "call_id": "fc_nyc",
                "name": "get_weather",
                "arguments": '{"location": "NYC"}',
            },
            {
                "type": "function_call",
                "call_id": "fc_la",
                "name": "get_weather",
                "arguments": '{"location": "LA"}',
            },
            {
                "type": "function_call_output",
                "call_id": "fc_nyc",
                "output": "Sunny, 72°F",
            },
            {
                "type": "function_call_output",
                "call_id": "fc_la",
                "output": "Cloudy, 65°F",
            },
        ]
        errors = validate_openai_responses_input(input_items, tools_defined=True)
        assert errors == []

    def test_pending_function_call_at_end_allowed(self):
        """Test that pending function_call at end is allowed (unlike Chat API)."""
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": "What's the weather?",
            },
            {
                "type": "function_call",
                "call_id": "fc_123",
                "name": "get_weather",
                "arguments": "{}",
            },
            # No function_call_output - this is OK for Responses API
        ]
        errors = validate_openai_responses_input(input_items, tools_defined=True)
        # Responses API allows this - model will continue from here
        assert errors == []

    def test_duplicate_function_call_id_rejected(self):
        """Test that duplicate function_call call_id is rejected."""
        input_items = [
            {
                "type": "function_call",
                "call_id": "fc_same",
                "name": "tool_a",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "fc_same",
                "output": "Result 1",
            },
            {
                "type": "function_call",
                "call_id": "fc_same",  # Same call_id again
                "name": "tool_b",
                "arguments": "{}",
            },
        ]
        errors = validate_openai_responses_input(input_items, tools_defined=True)
        assert any("Duplicate function_call" in e for e in errors)


class TestOpenAIResponsesRequest:
    """Tests for OpenAIResponsesRequest pydantic model."""

    def test_valid_request(self):
        """Test creating a valid request."""
        request = OpenAIResponsesRequest(
            model="gpt-4.1",
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": "Hello",
                },
            ],
        )
        assert request.model == "gpt-4.1"
        assert len(request.input) == 1

    def test_invalid_request_rejected(self):
        """Test that invalid request is rejected."""
        with pytest.raises(ValueError) as exc_info:
            OpenAIResponsesRequest(
                model="gpt-4.1",
                input=[
                    {
                        "type": "function_call_output",
                        "call_id": "fc_orphan",
                        "output": "Orphan output",
                    },
                ],
                tools=[
                    {
                        "type": "function",
                        "name": "test_tool",
                    }
                ],
            )
        assert "does not match any preceding function_call" in str(exc_info.value)

    def test_temperature_bounds(self):
        """Test temperature parameter bounds."""
        with pytest.raises(ValueError):
            OpenAIResponsesRequest(
                model="gpt-4.1",
                input=[{"type": "message", "role": "user", "content": "Hello"}],
                temperature=3.0,  # Too high
            )

    def test_max_output_tokens_must_be_positive(self):
        """Test max_output_tokens must be positive."""
        with pytest.raises(ValueError):
            OpenAIResponsesRequest(
                model="gpt-4.1",
                input=[{"type": "message", "role": "user", "content": "Hello"}],
                max_output_tokens=0,
            )
