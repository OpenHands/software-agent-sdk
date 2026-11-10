from litellm import ResponseFunctionToolCall
from litellm.types.responses.main import OutputFunctionToolCall
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


class _ProviderFields(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ChatMessageMetadata(_ProviderFields):
    """LiteLLM deletes absent optional fields despite declaring them on Message."""

    reasoning_content: str | None = None
    thinking_blocks: list[dict[str, object]] | None = None


class NonNativeMessageMetadata(_ProviderFields):
    reasoning_content: str | None = None
    provider_specific_fields: object = None


class _OutputKind(_ProviderFields):
    type: object = None


class _TextPart(_OutputKind):
    text: str | None = None

    @field_validator("text", mode="before")
    @classmethod
    def ignore_empty_text(cls, value: object) -> object:
        return value or None


class MessageOutput(_ProviderFields):
    content: list[object] | None = None


class FunctionOutput(_ProviderFields):
    id: object = None
    call_id: str | None = None
    name: str = ""
    arguments: str = ""


class _ReasoningText(_ProviderFields):
    text: str = ""


class ReasoningOutput(_ProviderFields):
    id: str | None = None
    summary: list[_ReasoningText] | None = None
    content: list[_ReasoningText] | None = None
    encrypted_content: str | None = None
    status: str | None = None


ResponseOutput = (
    MessageOutput
    | FunctionOutput
    | ReasoningOutput
    | ResponseFunctionToolCall
    | OutputFunctionToolCall
)


def normalize_text_part(part: object) -> _TextPart | None:
    try:
        kind = _OutputKind.model_validate(part).type
    except ValidationError:
        return None
    if kind == "output_text":
        return _TextPart.model_validate(part)
    return None


def normalize_response_output(item: object) -> ResponseOutput | None:
    """Project provider objects and mappings onto the fields the SDK consumes."""
    try:
        kind = _OutputKind.model_validate(item).type
    except ValidationError:
        return None
    if kind == "message":
        return MessageOutput.model_validate(item)
    if kind == "function_call":
        if isinstance(item, (ResponseFunctionToolCall, OutputFunctionToolCall)):
            return item
        return FunctionOutput.model_validate(item)
    if kind == "reasoning":
        return ReasoningOutput.model_validate(item)
    return None
