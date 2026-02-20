"""Anthropic Messages API input validation schema.

This module provides strict Pydantic schemas for validating inputs before
sending them to the Anthropic Messages API.

Key constraints enforced:
1. Messages must alternate between user and assistant roles (after optional system)
2. tool_result blocks must immediately follow their corresponding tool_use blocks
3. tool_result blocks must come FIRST in user message content arrays
4. Each tool_use must have exactly one corresponding tool_result
5. No duplicate tool_use_ids in tool_result blocks

Reference: https://docs.anthropic.com/en/api/messages
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from openhands.sdk.llm.validation.base import BaseMessageValidator


# =============================================================================
# Content Block Types
# =============================================================================


class AnthropicTextBlock(BaseModel):
    """Text content block."""

    type: Literal["text"] = "text"
    text: str = Field(..., min_length=0)


class AnthropicImageSource(BaseModel):
    """Image source (base64 or URL)."""

    type: Literal["base64", "url"]
    media_type: Literal["image/jpeg", "image/png", "image/gif", "image/webp"] | None = (
        None
    )
    data: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "AnthropicImageSource":
        if self.type == "base64":
            if not self.data:
                raise ValueError("base64 image source requires 'data' field")
            if not self.media_type:
                raise ValueError("base64 image source requires 'media_type' field")
        elif self.type == "url":
            if not self.url:
                raise ValueError("url image source requires 'url' field")
        return self


class AnthropicImageBlock(BaseModel):
    """Image content block."""

    type: Literal["image"] = "image"
    source: AnthropicImageSource


class AnthropicToolUseBlock(BaseModel):
    """Tool use content block (from assistant)."""

    type: Literal["tool_use"] = "tool_use"
    id: str = Field(..., min_length=1, description="Unique tool use ID")
    name: str = Field(..., min_length=1, description="Tool name")
    input: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")


class AnthropicToolResultBlock(BaseModel):
    """Tool result content block (from user, responding to tool_use)."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = Field(..., min_length=1, description="ID of the tool_use block")
    content: str | list[AnthropicTextBlock | AnthropicImageBlock] | None = None
    is_error: bool | None = None


class AnthropicThinkingBlock(BaseModel):
    """Thinking block (from assistant with extended thinking)."""

    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str | None = None


class AnthropicRedactedThinkingBlock(BaseModel):
    """Redacted thinking block."""

    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str


# Union of all content block types
AnthropicContentBlock = (
    AnthropicTextBlock
    | AnthropicImageBlock
    | AnthropicToolUseBlock
    | AnthropicToolResultBlock
    | AnthropicThinkingBlock
    | AnthropicRedactedThinkingBlock
)


# =============================================================================
# Message Types
# =============================================================================


class AnthropicUserMessage(BaseModel):
    """User message with content blocks."""

    role: Literal["user"] = "user"
    content: str | list[AnthropicContentBlock]

    @field_validator("content", mode="after")
    @classmethod
    def validate_content_ordering(
        cls, v: str | list[AnthropicContentBlock]
    ) -> str | list[AnthropicContentBlock]:
        """Validate that tool_result blocks come FIRST in the content array."""
        if isinstance(v, str):
            return v

        seen_non_tool_result = False
        for block in v:
            if isinstance(block, AnthropicToolResultBlock):
                if seen_non_tool_result:
                    raise ValueError(
                        "tool_result blocks must come FIRST in user message content. "
                        "Found tool_result after other content types."
                    )
            else:
                seen_non_tool_result = True
        return v


class AnthropicAssistantMessage(BaseModel):
    """Assistant message with content blocks."""

    role: Literal["assistant"] = "assistant"
    content: str | list[AnthropicContentBlock]


AnthropicMessage = AnthropicUserMessage | AnthropicAssistantMessage


# =============================================================================
# Tool Definition
# =============================================================================


class AnthropicToolInputSchema(BaseModel):
    """JSON Schema for tool input parameters."""

    type: Literal["object"] = "object"
    properties: dict[str, Any] = Field(default_factory=dict)
    required: list[str] | None = None


class AnthropicTool(BaseModel):
    """Tool definition for Anthropic."""

    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    input_schema: AnthropicToolInputSchema


# =============================================================================
# Request Schema
# =============================================================================


class AnthropicMessageRequest(BaseModel):
    """Full Anthropic Messages API request schema.

    This validates the structure of a request before sending it to the API.
    """

    model: str = Field(..., min_length=1)
    messages: list[AnthropicMessage] = Field(..., min_length=1)
    max_tokens: int = Field(..., gt=0)
    system: str | None = None
    tools: list[AnthropicTool] | None = None
    temperature: float | None = Field(default=None, ge=0, le=1)
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=0)
    stop_sequences: list[str] | None = None
    stream: bool | None = None

    @model_validator(mode="after")
    def validate_message_sequence(self) -> "AnthropicMessageRequest":
        """Validate the complete message sequence for Anthropic requirements."""
        errors = validate_anthropic_messages(
            [m.model_dump() for m in self.messages],
            tools_defined=self.tools is not None and len(self.tools) > 0,
        )
        if errors:
            raise ValueError("Message validation errors:\n" + "\n".join(errors))
        return self


# =============================================================================
# Validation Functions
# =============================================================================


def validate_anthropic_messages(
    messages: list[dict[str, Any]],
    tools_defined: bool = False,
) -> list[str]:
    """Validate a sequence of Anthropic messages.

    This function checks:
    1. Messages alternate between user and assistant roles
    2. tool_use blocks have corresponding tool_result blocks in the next message
    3. tool_result blocks reference valid tool_use_ids from the previous message
    4. No duplicate tool_use_ids
    5. If tool_use/tool_result blocks exist, tools must be defined

    Args:
        messages: List of message dictionaries
        tools_defined: Whether tools are defined in the request

    Returns:
        List of error messages (empty if valid)
    """
    errors: list[str] = []

    if not messages:
        return ["messages list cannot be empty"]

    # Track all tool_use_ids that have been used
    all_tool_use_ids: set[str] = set()
    # Track tool_use_ids that need tool_result responses
    pending_tool_use_ids: set[str] = set()
    # Track tool_result_ids we've seen (for duplicate detection)
    seen_tool_result_ids: set[str] = set()

    prev_role: str | None = None

    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        # Check role alternation
        if prev_role is not None:
            if role == prev_role:
                errors.append(
                    f"messages[{i}]: Role '{role}' cannot follow itself. "
                    "Messages must alternate between 'user' and 'assistant'."
                )
        prev_role = role

        # First message must be 'user'
        if i == 0 and role != "user":
            errors.append(
                f"messages[0]: First message must have role 'user', got '{role}'"
            )

        # Extract content blocks
        content_blocks: list[dict[str, Any]] = []
        if isinstance(content, str):
            content_blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            content_blocks = content

        # Process based on role
        if role == "assistant":
            # Collect tool_use blocks from assistant message
            tool_use_ids_in_msg: list[str] = []
            for block in content_blocks:
                if block.get("type") == "tool_use":
                    tool_use_id = block.get("id")
                    if not tool_use_id:
                        errors.append(
                            f"messages[{i}]: tool_use block missing 'id' field"
                        )
                        continue

                    if tool_use_id in all_tool_use_ids:
                        errors.append(
                            f"messages[{i}]: Duplicate tool_use id '{tool_use_id}'"
                        )
                    else:
                        all_tool_use_ids.add(tool_use_id)
                        tool_use_ids_in_msg.append(tool_use_id)

                    if not tools_defined:
                        errors.append(
                            f"messages[{i}]: tool_use block found but no tools "
                            "defined. Must include tools parameter."
                        )

            # If there are tool_use blocks, they need tool_result in next message
            if tool_use_ids_in_msg:
                pending_tool_use_ids.update(tool_use_ids_in_msg)

        elif role == "user":
            # Check for tool_result blocks
            tool_result_ids_in_msg: set[str] = set()
            seen_non_tool_result = False

            for j, block in enumerate(content_blocks):
                if block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if not tool_use_id:
                        errors.append(
                            f"messages[{i}].content[{j}]: "
                            "tool_result block missing 'tool_use_id' field"
                        )
                        continue

                    # Check ordering - tool_result must come before other content
                    if seen_non_tool_result:
                        errors.append(
                            f"messages[{i}].content[{j}]: "
                            "tool_result blocks must come FIRST in user message "
                            "content. Found after other content types."
                        )

                    # Check for duplicate tool_result for same tool_use_id
                    if tool_use_id in seen_tool_result_ids:
                        errors.append(
                            f"messages[{i}].content[{j}]: "
                            f"Duplicate tool_result for tool_use_id '{tool_use_id}'. "
                            "Each tool_use must have exactly one tool_result."
                        )
                    else:
                        seen_tool_result_ids.add(tool_use_id)

                    tool_result_ids_in_msg.add(tool_use_id)

                    if not tools_defined:
                        errors.append(
                            f"messages[{i}].content[{j}]: "
                            "tool_result block found but no tools defined. "
                            "Must include tools parameter."
                        )
                else:
                    seen_non_tool_result = True

            # Check that tool_result blocks match pending tool_use blocks
            if pending_tool_use_ids:
                # All pending tool_use_ids must have tool_result in this message
                missing = pending_tool_use_ids - tool_result_ids_in_msg
                if missing:
                    errors.append(
                        f"messages[{i}]: Missing tool_result blocks for tool_use ids: "
                        f"{sorted(missing)}. Each tool_use block must have a "
                        "corresponding tool_result block in the next message."
                    )

                # Check for unexpected tool_result_ids
                unexpected = tool_result_ids_in_msg - pending_tool_use_ids
                if unexpected:
                    errors.append(
                        f"messages[{i}]: Unexpected tool_result blocks with ids: "
                        f"{sorted(unexpected)}. These do not match any tool_use "
                        "blocks from the previous assistant message."
                    )

                # Clear pending after processing
                pending_tool_use_ids.clear()
            elif tool_result_ids_in_msg:
                # tool_result without preceding tool_use
                errors.append(
                    f"messages[{i}]: Found tool_result blocks but no preceding "
                    f"tool_use blocks. ids: {sorted(tool_result_ids_in_msg)}"
                )

    # Check for unresolved tool_use at end
    if pending_tool_use_ids:
        errors.append(
            f"Conversation ends with unresolved tool_use blocks: "
            f"{sorted(pending_tool_use_ids)}. Each tool_use must have a "
            "corresponding tool_result in the following user message."
        )

    return errors


class AnthropicMessageValidator(BaseMessageValidator):
    """Anthropic-specific message validator.

    This implements Anthropic's stricter validation rules:
    - Messages must alternate between user and assistant
    - First message must be user
    - tool_result must immediately follow tool_use
    - tool_result blocks must come first in user content
    """

    provider: str = "anthropic"

    def validate(
        self,
        messages: list[dict[str, Any]],
        tools_defined: bool = False,
    ) -> list[str]:
        """Validate Anthropic Messages API message sequence."""
        return validate_anthropic_messages(messages, tools_defined=tools_defined)
