"""Base classes for LLM input validation.

This module provides abstract base classes and standard implementations
for validating LLM API inputs before sending requests.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseMessageValidator(ABC):
    """Abstract base class for message validation.

    Subclasses implement provider-specific validation rules.
    """

    provider: str = "generic"

    @abstractmethod
    def validate(
        self,
        messages: list[dict[str, Any]],
        tools_defined: bool = False,
    ) -> list[str]:
        """Validate a sequence of messages.

        Args:
            messages: List of message dictionaries
            tools_defined: Whether tools are defined in the request

        Returns:
            List of error messages (empty if valid)
        """
        ...

    def validate_or_raise(
        self,
        messages: list[dict[str, Any]],
        tools_defined: bool = False,
    ) -> None:
        """Validate messages and raise if invalid.

        Args:
            messages: List of message dictionaries
            tools_defined: Whether tools are defined in the request

        Raises:
            LLMInputValidationError: If validation fails
        """
        from openhands.sdk.llm.exceptions import LLMInputValidationError

        errors = self.validate(messages, tools_defined=tools_defined)
        if errors:
            raise LLMInputValidationError(errors=errors, provider=self.provider)


class ChatMessageValidator(BaseMessageValidator):
    """Standard validator for OpenAI-compatible Chat Completions APIs.

    This implements the common validation rules that apply to most
    OpenAI-compatible providers (OpenAI, Azure, most proxies).

    Rules:
    1. tool messages must follow assistant with matching tool_call_ids
    2. Each tool_call must have exactly one corresponding tool message
    3. Function arguments must be valid JSON
    4. No duplicate tool_call_ids
    """

    provider: str = "openai_chat"

    def validate(
        self,
        messages: list[dict[str, Any]],
        tools_defined: bool = False,
    ) -> list[str]:
        """Validate OpenAI Chat Completions message sequence."""
        from openhands.sdk.llm.validation.openai_chat import (
            validate_openai_chat_messages,
        )

        return validate_openai_chat_messages(messages, tools_defined=tools_defined)


class ResponsesInputValidator(BaseMessageValidator):
    """Standard validator for OpenAI-compatible Responses APIs.

    This implements validation for the Responses API format.

    Rules:
    1. function_call_output items must have matching function_call call_ids
    2. Each function_call has at most one function_call_output
    3. Function arguments must be valid JSON
    """

    provider: str = "openai_responses"

    def validate(
        self,
        messages: list[dict[str, Any]],
        tools_defined: bool = False,
    ) -> list[str]:
        """Validate OpenAI Responses API input sequence."""
        from openhands.sdk.llm.validation.openai_responses import (
            validate_openai_responses_input,
        )

        return validate_openai_responses_input(messages, tools_defined=tools_defined)


def _is_anthropic_model(model: str) -> bool:
    """Check if model is an Anthropic model."""
    model_lower = model.lower()
    return any(
        pattern in model_lower
        for pattern in [
            "anthropic",
            "claude",
            "claude-3",
            "claude-2",
            "claude-instant",
        ]
    )


def get_chat_validator(model: str | None = None) -> BaseMessageValidator:
    """Get the appropriate Chat validator for a model.

    Args:
        model: Model identifier (e.g., "anthropic/claude-3-opus", "gpt-4")

    Returns:
        Appropriate validator instance
    """
    if model and _is_anthropic_model(model):
        from openhands.sdk.llm.validation.anthropic import AnthropicMessageValidator

        return AnthropicMessageValidator()

    # Default to OpenAI Chat validator for all other models
    from openhands.sdk.llm.validation.openai_chat import OpenAIChatMessageValidator

    return OpenAIChatMessageValidator()


def get_responses_validator(model: str | None = None) -> BaseMessageValidator:
    """Get the appropriate Responses validator for a model.

    Args:
        model: Model identifier

    Returns:
        Appropriate validator instance

    Note:
        Currently all Responses API implementations use the same format,
        so this always returns the standard validator.
    """
    from openhands.sdk.llm.validation.openai_responses import (
        OpenAIResponsesInputValidator,
    )

    return OpenAIResponsesInputValidator()
