"""OpenAI Chat Completions API input validation schema.

This module provides strict Pydantic schemas for validating inputs before
sending them to the OpenAI Chat Completions API.

Key constraints enforced:
1. Tool messages must follow assistant messages with matching tool_call_ids
2. Each tool_call must have exactly one corresponding tool message
3. Function calls must have valid JSON arguments
4. System messages should come first (OpenAI allows system anywhere but first is best)

Reference: https://platform.openai.com/docs/api-reference/chat/create
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from openhands.sdk.llm.validation.base import BaseMessageValidator


# =============================================================================
# Content Part Types
# =============================================================================


class OpenAITextContentPart(BaseModel):
    """Text content part."""

    type: Literal["text"] = "text"
    text: str


class OpenAIImageURL(BaseModel):
    """Image URL details."""

    url: str
    detail: Literal["auto", "low", "high"] | None = None


class OpenAIImageContentPart(BaseModel):
    """Image content part."""

    type: Literal["image_url"] = "image_url"
    image_url: OpenAIImageURL


class OpenAIInputAudioContentPart(BaseModel):
    """Audio content part."""

    type: Literal["input_audio"] = "input_audio"
    input_audio: dict[str, Any]  # Contains data and format


OpenAIContentPart = (
    OpenAITextContentPart | OpenAIImageContentPart | OpenAIInputAudioContentPart
)


# =============================================================================
# Tool Call Types
# =============================================================================


class OpenAIFunction(BaseModel):
    """Function details in a tool call."""

    name: str = Field(..., min_length=1)
    arguments: str  # JSON string

    @model_validator(mode="after")
    def validate_arguments_json(self) -> "OpenAIFunction":
        """Validate that arguments is valid JSON."""
        try:
            json.loads(self.arguments)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"tool_call function arguments must be valid JSON: {e}"
            ) from e
        return self


class OpenAIToolCall(BaseModel):
    """Tool call from assistant."""

    id: str = Field(..., min_length=1, description="Unique tool call ID")
    type: Literal["function"] = "function"
    function: OpenAIFunction


# =============================================================================
# Message Types
# =============================================================================


class OpenAISystemMessage(BaseModel):
    """System message."""

    role: Literal["system"] = "system"
    content: str | list[OpenAITextContentPart]
    name: str | None = None


class OpenAIUserMessage(BaseModel):
    """User message."""

    role: Literal["user"] = "user"
    content: str | list[OpenAIContentPart]
    name: str | None = None


class OpenAIAssistantMessage(BaseModel):
    """Assistant message."""

    role: Literal["assistant"] = "assistant"
    content: str | list[OpenAITextContentPart] | None = None
    name: str | None = None
    tool_calls: list[OpenAIToolCall] | None = None
    refusal: str | None = None

    @model_validator(mode="after")
    def validate_content_or_tool_calls(self) -> "OpenAIAssistantMessage":
        """Validate that either content or tool_calls is present."""
        if self.content is None and self.tool_calls is None:
            raise ValueError(
                "Assistant message must have either 'content' or 'tool_calls'"
            )
        return self


class OpenAIToolMessage(BaseModel):
    """Tool message (response to tool_call)."""

    role: Literal["tool"] = "tool"
    content: str | list[OpenAITextContentPart]
    tool_call_id: str = Field(..., min_length=1)


# Deprecated function message type (for backward compatibility)
class OpenAIFunctionMessage(BaseModel):
    """Function message (deprecated, use tool message instead)."""

    role: Literal["function"] = "function"
    content: str | None
    name: str


OpenAIMessage = (
    OpenAISystemMessage
    | OpenAIUserMessage
    | OpenAIAssistantMessage
    | OpenAIToolMessage
    | OpenAIFunctionMessage
)


# =============================================================================
# Tool Definition
# =============================================================================


class OpenAIFunctionDefinition(BaseModel):
    """Function definition for tools."""

    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    parameters: dict[str, Any] | None = None
    strict: bool | None = None


class OpenAIToolDefinition(BaseModel):
    """Tool definition."""

    type: Literal["function"] = "function"
    function: OpenAIFunctionDefinition


# =============================================================================
# Request Schema
# =============================================================================


class OpenAIChatCompletionRequest(BaseModel):
    """Full OpenAI Chat Completions API request schema."""

    model: str = Field(..., min_length=1)
    messages: list[OpenAIMessage] = Field(..., min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    n: int | None = Field(default=None, ge=1)
    stream: bool | None = None
    stop: str | list[str] | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    max_completion_tokens: int | None = Field(default=None, gt=0)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    logit_bias: dict[str, float] | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = Field(default=None, ge=0, le=20)
    user: str | None = None
    tools: list[OpenAIToolDefinition] | None = None
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    seed: int | None = None
    response_format: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_message_sequence(self) -> "OpenAIChatCompletionRequest":
        """Validate the complete message sequence."""
        errors = validate_openai_chat_messages(
            [m.model_dump() for m in self.messages],
            tools_defined=self.tools is not None and len(self.tools) > 0,
        )
        if errors:
            raise ValueError("Message validation errors:\n" + "\n".join(errors))
        return self


# =============================================================================
# Validation Functions
# =============================================================================


def validate_openai_chat_messages(
    messages: list[dict[str, Any]],
    tools_defined: bool = False,
) -> list[str]:
    """Validate a sequence of OpenAI Chat Completion messages.

    This function checks:
    1. Tool messages have matching tool_call_ids from preceding assistant message
    2. Each tool_call has exactly one corresponding tool message
    3. No orphan tool messages (tool_call_id not in previous assistant's tool_calls)
    4. System messages come first (warning, not error - OpenAI allows anywhere)

    Args:
        messages: List of message dictionaries
        tools_defined: Whether tools are defined in the request

    Returns:
        List of error messages (empty if valid)
    """
    errors: list[str] = []

    if not messages:
        return ["messages list cannot be empty"]

    # Track tool_call_ids from the most recent assistant message
    pending_tool_call_ids: set[str] = set()
    # Track all tool_call_ids we've seen responses for
    responded_tool_call_ids: set[str] = set()
    # Track tool_call_ids for the current batch (to detect duplicates in responses)
    current_batch_tool_call_ids: set[str] = set()

    seen_non_system = False

    for i, msg in enumerate(messages):
        role = msg.get("role")

        # Check system message positioning (warning level)
        if role == "system":
            if seen_non_system:
                # This is a warning, not an error - OpenAI allows it
                pass  # Could add to a warnings list if desired
        else:
            seen_non_system = True

        if role == "assistant":
            # Check for unresolved tool_calls from previous assistant
            if pending_tool_call_ids:
                errors.append(
                    f"messages[{i}]: Previous assistant message had unresolved "
                    f"tool_calls: {sorted(pending_tool_call_ids)}. "
                    "Each tool_call must have a corresponding tool message."
                )

            # Collect tool_calls from this assistant message
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                pending_tool_call_ids.clear()
                current_batch_tool_call_ids.clear()

                for j, tc in enumerate(tool_calls):
                    tc_id = tc.get("id")
                    if not tc_id:
                        errors.append(
                            f"messages[{i}].tool_calls[{j}]: Missing 'id' field"
                        )
                        continue

                    if tc_id in current_batch_tool_call_ids:
                        errors.append(
                            f"messages[{i}].tool_calls[{j}]: "
                            f"Duplicate tool_call id '{tc_id}' in same message"
                        )
                    else:
                        current_batch_tool_call_ids.add(tc_id)
                        pending_tool_call_ids.add(tc_id)

                    # Validate function arguments are JSON
                    func = tc.get("function", {})
                    args = func.get("arguments", "")
                    if args:
                        try:
                            json.loads(args)
                        except json.JSONDecodeError:
                            errors.append(
                                f"messages[{i}].tool_calls[{j}]: "
                                f"function.arguments is not valid JSON"
                            )

                if not tools_defined and tool_calls:
                    errors.append(
                        f"messages[{i}]: Assistant has tool_calls but no tools defined"
                    )

        elif role == "tool":
            tool_call_id = msg.get("tool_call_id")

            if not tool_call_id:
                errors.append(f"messages[{i}]: Tool message missing 'tool_call_id'")
                continue

            # Check if this tool_call_id was in the pending set
            if tool_call_id not in pending_tool_call_ids:
                # Check if it was already responded to
                if tool_call_id in responded_tool_call_ids:
                    errors.append(
                        f"messages[{i}]: Duplicate tool response for "
                        f"tool_call_id '{tool_call_id}'. "
                        "Each tool_call should have exactly one response."
                    )
                else:
                    errors.append(
                        f"messages[{i}]: Tool message with tool_call_id "
                        f"'{tool_call_id}' does not match any pending tool_call. "
                        "Tool messages must follow an assistant message with "
                        "matching tool_calls."
                    )
            else:
                pending_tool_call_ids.remove(tool_call_id)
                responded_tool_call_ids.add(tool_call_id)

            if not tools_defined:
                errors.append(
                    f"messages[{i}]: Tool message found but no tools defined"
                )

    # Check for unresolved tool_calls at the end
    if pending_tool_call_ids:
        errors.append(
            f"Conversation ends with unresolved tool_calls: "
            f"{sorted(pending_tool_call_ids)}. "
            "Each tool_call must have a corresponding tool message."
        )

    return errors


class OpenAIChatMessageValidator(BaseMessageValidator):
    """OpenAI Chat Completions message validator.

    This implements standard OpenAI Chat validation rules,
    which are also compatible with most other providers.
    """

    provider: str = "openai_chat"

    def validate(
        self,
        messages: list[dict[str, Any]],
        tools_defined: bool = False,
    ) -> list[str]:
        """Validate OpenAI Chat Completions message sequence."""
        return validate_openai_chat_messages(messages, tools_defined=tools_defined)
