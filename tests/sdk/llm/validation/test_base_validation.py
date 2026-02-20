"""Tests for base validation classes and factory functions."""

import pytest

from openhands.sdk.llm.exceptions import LLMInputValidationError
from openhands.sdk.llm.validation import (
    AnthropicMessageValidator,
    BaseMessageValidator,
    ChatMessageValidator,
    OpenAIChatMessageValidator,
    OpenAIResponsesInputValidator,
    ResponsesInputValidator,
    get_chat_validator,
    get_responses_validator,
)


class TestValidatorFactoryFunctions:
    """Tests for validator factory functions."""

    def test_get_chat_validator_anthropic(self):
        """Test that Anthropic models get the Anthropic validator."""
        validator = get_chat_validator("anthropic/claude-3-opus")
        assert isinstance(validator, AnthropicMessageValidator)
        assert validator.provider == "anthropic"

    def test_get_chat_validator_anthropic_variants(self):
        """Test various Anthropic model name patterns."""
        models = [
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-instant-1.2",
            "anthropic/claude-2.1",
            "Claude-3-Haiku",  # Case insensitive
        ]
        for model in models:
            validator = get_chat_validator(model)
            assert isinstance(
                validator, AnthropicMessageValidator
            ), f"Expected AnthropicMessageValidator for {model}"

    def test_get_chat_validator_openai(self):
        """Test that OpenAI models get the OpenAI validator."""
        validator = get_chat_validator("gpt-4")
        assert isinstance(validator, OpenAIChatMessageValidator)
        assert validator.provider == "openai_chat"

    def test_get_chat_validator_openai_variants(self):
        """Test various OpenAI model name patterns."""
        models = [
            "gpt-4",
            "gpt-4o",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
            "openai/gpt-4",
        ]
        for model in models:
            validator = get_chat_validator(model)
            assert isinstance(
                validator, OpenAIChatMessageValidator
            ), f"Expected OpenAIChatMessageValidator for {model}"

    def test_get_chat_validator_other_providers(self):
        """Test that other providers get the default OpenAI validator."""
        models = [
            "gemini-pro",
            "mistral-7b",
            "llama-2-70b-chat",
            "deepseek-chat",
        ]
        for model in models:
            validator = get_chat_validator(model)
            # Default is OpenAI-compatible
            assert isinstance(
                validator, OpenAIChatMessageValidator
            ), f"Expected OpenAIChatMessageValidator for {model}"

    def test_get_chat_validator_none_model(self):
        """Test that None model returns default validator."""
        validator = get_chat_validator(None)
        assert isinstance(validator, OpenAIChatMessageValidator)

    def test_get_responses_validator(self):
        """Test that responses validator is always OpenAI-compatible."""
        validator = get_responses_validator("gpt-4")
        assert isinstance(validator, OpenAIResponsesInputValidator)
        assert validator.provider == "openai_responses"

    def test_get_responses_validator_none_model(self):
        """Test that None model returns default responses validator."""
        validator = get_responses_validator(None)
        assert isinstance(validator, OpenAIResponsesInputValidator)


class TestValidatorOrRaise:
    """Tests for validate_or_raise method."""

    def test_validate_or_raise_valid(self):
        """Test that valid messages don't raise."""
        validator = OpenAIChatMessageValidator()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        # Should not raise
        validator.validate_or_raise(messages)

    def test_validate_or_raise_invalid(self):
        """Test that invalid messages raise LLMInputValidationError."""
        validator = OpenAIChatMessageValidator()
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "tool",
                "content": "Orphan tool message",
                "tool_call_id": "no_match",
            },
        ]
        with pytest.raises(LLMInputValidationError) as exc_info:
            validator.validate_or_raise(messages, tools_defined=True)

        assert exc_info.value.provider == "openai_chat"
        assert len(exc_info.value.errors) > 0
        assert "no_match" in str(exc_info.value)


class TestLLMInputValidationError:
    """Tests for LLMInputValidationError exception."""

    def test_error_message_formatting(self):
        """Test that error messages are properly formatted."""
        errors = [
            "First error",
            "Second error",
        ]
        exc = LLMInputValidationError(errors=errors, provider="test_provider")

        assert exc.provider == "test_provider"
        assert exc.errors == errors
        assert "(test_provider)" in str(exc)
        assert "First error" in str(exc)
        assert "Second error" in str(exc)

    def test_error_without_provider(self):
        """Test error message without provider."""
        errors = ["Some error"]
        exc = LLMInputValidationError(errors=errors)

        assert exc.provider is None
        assert "Some error" in str(exc)
        # Provider placeholder should not appear
        assert "()" not in str(exc)

    def test_error_custom_message(self):
        """Test error with custom message."""
        exc = LLMInputValidationError(
            errors=["Error 1"],
            provider="custom",
            message="Custom error message",
        )
        assert str(exc) == "Custom error message"
        assert exc.errors == ["Error 1"]
        assert exc.provider == "custom"


class TestBaseMessageValidatorInterface:
    """Tests for BaseMessageValidator abstract interface."""

    def test_chat_validator_is_base(self):
        """Test that ChatMessageValidator is a BaseMessageValidator."""
        validator = ChatMessageValidator()
        assert isinstance(validator, BaseMessageValidator)

    def test_responses_validator_is_base(self):
        """Test that ResponsesInputValidator is a BaseMessageValidator."""
        validator = ResponsesInputValidator()
        assert isinstance(validator, BaseMessageValidator)

    def test_anthropic_validator_is_base(self):
        """Test that AnthropicMessageValidator is a BaseMessageValidator."""
        validator = AnthropicMessageValidator()
        assert isinstance(validator, BaseMessageValidator)

    def test_all_validators_have_provider(self):
        """Test that all validators have a provider attribute."""
        validators = [
            ChatMessageValidator(),
            ResponsesInputValidator(),
            AnthropicMessageValidator(),
            OpenAIChatMessageValidator(),
            OpenAIResponsesInputValidator(),
        ]
        for validator in validators:
            assert hasattr(validator, "provider")
            assert isinstance(validator.provider, str)
            assert len(validator.provider) > 0
