"""LLM input validation schemas.

This module provides strict Pydantic schemas for validating inputs before
sending them to LLM provider APIs. The goal is to catch errors locally
before they result in API errors.

The schemas are intentionally stricter than the actual API specifications
to catch common mistakes early.

Architecture:
- BaseMessageValidator: Abstract base class defining the validation interface
- ChatMessageValidator: Standard validator for OpenAI-compatible Chat APIs
- ResponsesInputValidator: Standard validator for OpenAI-compatible Responses APIs
- AnthropicMessageValidator: Anthropic-specific validator (stricter ordering rules)

Usage:
    from openhands.sdk.llm.validation import get_chat_validator, get_responses_validator

    # Get appropriate validator based on model
    validator = get_chat_validator(model="anthropic/claude-3-opus")
    errors = validator.validate(messages, tools_defined=True)
"""

from openhands.sdk.llm.validation.base import (
    BaseMessageValidator,
    ChatMessageValidator,
    ResponsesInputValidator,
    get_chat_validator,
    get_responses_validator,
)
from openhands.sdk.llm.validation.anthropic import (
    AnthropicMessageRequest,
    AnthropicMessageValidator,
    validate_anthropic_messages,
)
from openhands.sdk.llm.validation.openai_chat import (
    OpenAIChatCompletionRequest,
    OpenAIChatMessageValidator,
    validate_openai_chat_messages,
)
from openhands.sdk.llm.validation.openai_responses import (
    OpenAIResponsesRequest,
    OpenAIResponsesInputValidator,
    validate_openai_responses_input,
)

__all__ = [
    # Base classes
    "BaseMessageValidator",
    "ChatMessageValidator",
    "ResponsesInputValidator",
    # Factory functions
    "get_chat_validator",
    "get_responses_validator",
    # Anthropic
    "AnthropicMessageRequest",
    "AnthropicMessageValidator",
    "validate_anthropic_messages",
    # OpenAI Chat
    "OpenAIChatCompletionRequest",
    "OpenAIChatMessageValidator",
    "validate_openai_chat_messages",
    # OpenAI Responses
    "OpenAIResponsesRequest",
    "OpenAIResponsesInputValidator",
    "validate_openai_responses_input",
]
