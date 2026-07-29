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


class OpenAIModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[OpenAIModel]
