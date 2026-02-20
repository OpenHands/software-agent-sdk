"""OpenAI Responses API input validation schema.

This module provides strict Pydantic schemas for validating inputs before
sending them to the OpenAI Responses API.

Key constraints enforced:
1. function_call_output items must have matching function_call items
2. call_id references must be valid
3. Input items must follow proper sequencing

Reference: https://platform.openai.com/docs/api-reference/responses
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from openhands.sdk.llm.validation.base import BaseMessageValidator


# =============================================================================
# Input Item Types
# =============================================================================


class OpenAIResponsesTextContent(BaseModel):
    """Text content in a message."""

    type: Literal["input_text", "output_text"]
    text: str


class OpenAIResponsesImageContent(BaseModel):
    """Image content."""

    type: Literal["input_image"] = "input_image"
    image_url: str | None = None
    file_id: str | None = None
    detail: Literal["auto", "low", "high"] | None = None


class OpenAIResponsesMessage(BaseModel):
    """Message item in Responses API."""

    type: Literal["message"] = "message"
    role: Literal["user", "assistant", "system"]
    content: str | list[OpenAIResponsesTextContent | OpenAIResponsesImageContent]


class OpenAIResponsesFunctionCall(BaseModel):
    """Function call item (assistant requesting tool execution)."""

    type: Literal["function_call"] = "function_call"
    id: str | None = None
    call_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    arguments: str  # JSON string

    @model_validator(mode="after")
    def validate_arguments_json(self) -> "OpenAIResponsesFunctionCall":
        """Validate that arguments is valid JSON."""
        try:
            json.loads(self.arguments)
        except json.JSONDecodeError as e:
            raise ValueError(f"function_call arguments must be valid JSON: {e}") from e
        return self


class OpenAIResponsesFunctionCallOutput(BaseModel):
    """Function call output item (response to function_call)."""

    type: Literal["function_call_output"] = "function_call_output"
    call_id: str = Field(..., min_length=1)
    output: str | list[dict[str, Any]]


class OpenAIResponsesReasoningItem(BaseModel):
    """Reasoning item (for reasoning models)."""

    type: Literal["reasoning"] = "reasoning"
    id: str | None = None
    summary: list[dict[str, Any]] | None = None
    encrypted_content: str | None = None


# Union of input item types
OpenAIResponsesInputItem = (
    OpenAIResponsesMessage
    | OpenAIResponsesFunctionCall
    | OpenAIResponsesFunctionCallOutput
    | OpenAIResponsesReasoningItem
)


# =============================================================================
# Tool Definition
# =============================================================================


class OpenAIResponsesFunctionParameters(BaseModel):
    """Function parameters schema."""

    type: Literal["object"] = "object"
    properties: dict[str, Any] = Field(default_factory=dict)
    required: list[str] | None = None
    additionalProperties: bool | None = None


class OpenAIResponsesFunctionTool(BaseModel):
    """Function tool definition."""

    type: Literal["function"] = "function"
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    parameters: OpenAIResponsesFunctionParameters | None = None
    strict: bool | None = None


# =============================================================================
# Request Schema
# =============================================================================


class OpenAIResponsesRequest(BaseModel):
    """Full OpenAI Responses API request schema."""

    model: str = Field(..., min_length=1)
    input: list[OpenAIResponsesInputItem] = Field(..., min_length=1)
    instructions: str | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    tools: list[OpenAIResponsesFunctionTool] | None = None
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    reasoning: dict[str, Any] | None = None
    stream: bool | None = None
    truncation: str | None = None
    user: str | None = None
    metadata: dict[str, str] | None = None
    store: bool | None = None

    @model_validator(mode="after")
    def validate_input_sequence(self) -> "OpenAIResponsesRequest":
        """Validate the input sequence."""
        errors = validate_openai_responses_input(
            [item.model_dump() for item in self.input],
            tools_defined=self.tools is not None and len(self.tools) > 0,
        )
        if errors:
            raise ValueError("Input validation errors:\n" + "\n".join(errors))
        return self


# =============================================================================
# Validation Functions
# =============================================================================


def validate_openai_responses_input(
    input_items: list[dict[str, Any]],
    tools_defined: bool = False,
) -> list[str]:
    """Validate a sequence of OpenAI Responses API input items.

    This function checks:
    1. function_call_output items have matching function_call call_ids
    2. Each function_call has at most one function_call_output
    3. No orphan function_call_output items
    4. No duplicate function_call call_ids

    Args:
        input_items: List of input item dictionaries
        tools_defined: Whether tools are defined in the request

    Returns:
        List of error messages (empty if valid)
    """
    errors: list[str] = []

    if not input_items:
        return ["input list cannot be empty"]

    # Track pending function_call call_ids (not yet responded to)
    pending_call_ids: set[str] = set()
    # Track all function_call call_ids we've ever seen (for duplicate detection)
    all_call_ids: set[str] = set()
    # Track all function_call_output call_ids we've seen
    responded_call_ids: set[str] = set()

    for i, item in enumerate(input_items):
        item_type = item.get("type")

        if item_type == "function_call":
            call_id = item.get("call_id") or item.get("id")
            if not call_id:
                errors.append(
                    f"input[{i}]: function_call missing 'call_id' or 'id' field"
                )
                continue

            if call_id in all_call_ids:
                errors.append(
                    f"input[{i}]: Duplicate function_call with call_id '{call_id}'"
                )
            else:
                all_call_ids.add(call_id)
                pending_call_ids.add(call_id)

            # Validate arguments are JSON
            args = item.get("arguments", "")
            if args:
                try:
                    json.loads(args)
                except json.JSONDecodeError:
                    errors.append(
                        f"input[{i}]: function_call arguments is not valid JSON"
                    )

            if not tools_defined:
                errors.append(f"input[{i}]: function_call found but no tools defined")

        elif item_type == "function_call_output":
            call_id = item.get("call_id")
            if not call_id:
                errors.append(
                    f"input[{i}]: function_call_output missing 'call_id' field"
                )
                continue

            if call_id not in pending_call_ids:
                if call_id in responded_call_ids:
                    errors.append(
                        f"input[{i}]: Duplicate function_call_output for "
                        f"call_id '{call_id}'. Each function_call should have "
                        "at most one output."
                    )
                else:
                    errors.append(
                        f"input[{i}]: function_call_output with call_id "
                        f"'{call_id}' does not match any preceding function_call"
                    )
            else:
                pending_call_ids.remove(call_id)
                responded_call_ids.add(call_id)

            if not tools_defined:
                errors.append(
                    f"input[{i}]: function_call_output found but no tools defined"
                )

    # Note: Unlike Chat Completions, Responses API allows pending function_calls
    # at the end (the model will continue from there). So we don't error on this.

    return errors


class OpenAIResponsesInputValidator(BaseMessageValidator):
    """OpenAI Responses API input validator.

    This implements validation for the Responses API format.
    """

    provider: str = "openai_responses"

    def validate(
        self,
        messages: list[dict[str, Any]],
        tools_defined: bool = False,
    ) -> list[str]:
        """Validate OpenAI Responses API input sequence."""
        return validate_openai_responses_input(messages, tools_defined=tools_defined)
