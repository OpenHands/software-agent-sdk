"""Models for the OpenAI-compatible agent-server gateway."""

from typing import Literal

from openai.types import CompletionUsage, Model
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice, ChoiceDelta
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.responses import (
    Response,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import (
    InputTokensDetails,
    OutputTokensDetails,
)
from pydantic import BaseModel, ConfigDict


OpenAIChatCompletionChoice = Choice
OpenAIChatCompletionChunk = ChatCompletionChunk
OpenAIChatCompletionChunkChoice = ChunkChoice
OpenAIChatCompletionChunkChoiceDelta = ChoiceDelta
OpenAIChatCompletionResponse = ChatCompletion
OpenAIModel = Model
OpenAIResponseMessage = ChatCompletionMessage
OpenAIUsage = CompletionUsage
OpenAIResponse = Response
OpenAIResponseOutputMessage = ResponseOutputMessage
OpenAIResponseOutputText = ResponseOutputText
OpenAIResponseUsage = ResponseUsage
OpenAIResponseInputTokensDetails = InputTokensDetails
OpenAIResponseOutputTokensDetails = OutputTokensDetails


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
