"""LLM input validation.

Validates inputs before sending to LLM APIs to catch errors locally.

Usage:
    from openhands.sdk.llm.validation import get_validator

    validator = get_validator(model="claude-3-opus", response_type="completion")
    errors = validator.validate(messages, tools_defined=True)
"""

from openhands.sdk.llm.validation.anthropic import AnthropicMessageValidator
from openhands.sdk.llm.validation.base import (
    BaseMessageValidator,
    ChatMessageValidator,
    ResponsesInputValidator,
    get_chat_validator,
    get_responses_validator,
    get_validator,
)
from openhands.sdk.llm.validation.openai_chat import OpenAIChatMessageValidator
from openhands.sdk.llm.validation.openai_responses import OpenAIResponsesInputValidator


__all__ = [
    # Main factory
    "get_validator",
    # Legacy factories
    "get_chat_validator",
    "get_responses_validator",
    # Base classes
    "BaseMessageValidator",
    "ChatMessageValidator",
    "ResponsesInputValidator",
    # Validators
    "AnthropicMessageValidator",
    "OpenAIChatMessageValidator",
    "OpenAIResponsesInputValidator",
]
