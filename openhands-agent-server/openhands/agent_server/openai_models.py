"""Models for the OpenAI-compatible agent-server gateway."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OpenAIImageURL(BaseModel):
    url: str


class OpenAIContentPart(BaseModel):
    type: str
    text: str | None = None
    image_url: OpenAIImageURL | str | None = None

    model_config = ConfigDict(extra="ignore")


class OpenAIChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[OpenAIContentPart] | None = None

    model_config = ConfigDict(extra="ignore")


class OpenAIChatCompletionRequest(BaseModel):
    model: str
    messages: list[OpenAIChatMessage]
    stream: bool = False

    model_config = ConfigDict(extra="ignore")


class OpenAIResponseMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class OpenAIChatCompletionChoice(BaseModel):
    index: int
    message: OpenAIResponseMessage
    finish_reason: Literal["stop"] = "stop"


class OpenAIUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[OpenAIChatCompletionChoice]
    usage: OpenAIUsage = Field(default_factory=OpenAIUsage)


class OpenAIModel(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: Literal["openhands"] = "openhands"


class OpenAIModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[OpenAIModel]
