"""Models for the OpenAI-compatible agent-server gateway."""

from typing import Literal

from openai.types import CompletionUsage, Model
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice, ChoiceDelta
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from pydantic import BaseModel, ConfigDict


OpenAIChatCompletionChoice = Choice
OpenAIChatCompletionChunk = ChatCompletionChunk
OpenAIChatCompletionChunkChoice = ChunkChoice
OpenAIChatCompletionChunkChoiceDelta = ChoiceDelta
OpenAIChatCompletionResponse = ChatCompletion
OpenAIModel = Model
OpenAIResponseMessage = ChatCompletionMessage
OpenAIUsage = CompletionUsage


class OpenAIResponseInputTokensDetails(BaseModel):
    cached_tokens: int


class OpenAIResponseOutputTokensDetails(BaseModel):
    reasoning_tokens: int


class OpenAIResponseUsage(BaseModel):
    input_tokens: int
    input_tokens_details: OpenAIResponseInputTokensDetails
    output_tokens: int
    output_tokens_details: OpenAIResponseOutputTokensDetails
    total_tokens: int


class OpenAIResponseOutputText(BaseModel):
    annotations: list[dict[str, object]]
    text: str
    type: Literal["output_text"] = "output_text"


class OpenAIResponseOutputMessage(BaseModel):
    id: str
    content: list[OpenAIResponseOutputText]
    role: Literal["assistant"] = "assistant"
    status: Literal["completed"] = "completed"
    type: Literal["message"] = "message"


class OpenAIResponse(BaseModel):
    id: str
    created_at: float
    completed_at: float
    instructions: str | None = None
    metadata: dict[str, str] | None = None
    model: str
    object: Literal["response"] = "response"
    output: list[OpenAIResponseOutputMessage]
    parallel_tool_calls: bool = False
    previous_response_id: str | None = None
    status: Literal["completed"] = "completed"
    tool_choice: Literal["none"] = "none"
    tools: list[dict[str, str]]
    usage: OpenAIResponseUsage


class OpenAIImageURL(BaseModel):
    url: str


class OpenAIContentPart(BaseModel):
    type: str
    text: str | None = None
    image_url: OpenAIImageURL | str | None = None

    model_config = ConfigDict(extra="ignore")


class OpenAIChatMessage(BaseModel):
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[OpenAIContentPart] | None = None

    model_config = ConfigDict(extra="ignore")


class OpenAIStreamOptions(BaseModel):
    include_usage: bool = False

    model_config = ConfigDict(extra="ignore")


class OpenAIChatCompletionRequest(BaseModel):
    model: str
    messages: list[OpenAIChatMessage]
    stream: bool = False
    stream_options: OpenAIStreamOptions | None = None

    model_config = ConfigDict(extra="ignore")


class OpenAIResponseInputContentPart(BaseModel):
    type: str
    text: str | None = None
    image_url: str | None = None

    model_config = ConfigDict(extra="ignore")


class OpenAIResponseInputMessage(BaseModel):
    role: Literal["system", "developer", "user", "assistant"]
    content: str | list[OpenAIResponseInputContentPart]

    model_config = ConfigDict(extra="ignore")


class OpenAIResponseRequest(BaseModel):
    model: str
    input: str | list[OpenAIResponseInputMessage]
    instructions: str | None = None
    previous_response_id: str | None = None
    store: bool = False
    stream: bool = False
    metadata: dict[str, str] | None = None

    model_config = ConfigDict(extra="ignore")


class OpenAIModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[OpenAIModel]
