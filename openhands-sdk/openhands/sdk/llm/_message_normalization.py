from litellm import ResponseFunctionToolCall
from litellm.types.llms.openai import ResponsesAPIResponse
from litellm.types.responses.main import OutputFunctionToolCall
from openai.types.responses.response import IncompleteDetails
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


def stopped_by_content_filter(response: ResponsesAPIResponse) -> bool:
    # LiteLLM can retain raw mappings for partially validated provider responses.
    match response.get("incomplete_details"):
        case IncompleteDetails(reason="content_filter") | {"reason": "content_filter"}:
            return True
    return False


def _output_kind(value: object) -> object:
    try:
        return _OutputKind.model_validate(value).type
    except ValidationError:
        return None


def response_is_refused(response: ResponsesAPIResponse) -> bool:
    if stopped_by_content_filter(response):
        return True
    for item in response.output or []:
        if _output_kind(item) == "message":
            parts = MessageOutput.model_validate(item).content or []
            if any(_output_kind(part) == "refusal" for part in parts):
                return True
    return False


def normalize_text_part(part: object) -> _TextPart | None:
    if _output_kind(part) == "output_text":
        return _TextPart.model_validate(part)
    return None


def normalize_response_output(item: object) -> ResponseOutput | None:
    """Project provider objects and mappings onto the fields the SDK consumes."""
    kind = _output_kind(item)
    if kind == "message":
        return MessageOutput.model_validate(item)
    if kind == "function_call":
        if isinstance(item, (ResponseFunctionToolCall, OutputFunctionToolCall)):
            return item
        return FunctionOutput.model_validate(item)
    if kind == "reasoning":
        return ReasoningOutput.model_validate(item)
    return None
