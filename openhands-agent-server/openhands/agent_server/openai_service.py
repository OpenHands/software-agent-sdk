"""Service logic for the OpenAI-compatible agent-server gateway."""

import asyncio
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.openai_models import (
    OpenAIChatCompletionChoice,
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIChatMessage,
    OpenAIModel,
    OpenAIModelListResponse,
    OpenAIResponseMessage,
    OpenAIUsage,
)
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk import LLM, Message
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.request import (
    SendMessageRequest,
    StartConversationRequest,
)
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.sdk.settings import ACPAgentSettings, OpenHandsAgentSettings
from openhands.sdk.workspace import LocalWorkspace


_MODEL_PREFIX = "openhands_"
_DEFAULT_WORKSPACE = "workspace/project"
_GATEWAY_TIMEOUT_SECONDS = 120.0
_POLL_INTERVAL_SECONDS = 2


@dataclass(frozen=True)
class OpenAIChatCompletionResult:
    response: OpenAIChatCompletionResponse
    conversation_id: UUID


def _profile_name_from_model(model: str) -> str:
    if model.startswith(_MODEL_PREFIX) and len(model) > len(_MODEL_PREFIX):
        return model[len(_MODEL_PREFIX) :]
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Unknown OpenHands model '{model}'. Use GET /v1/models.",
    )


def _load_profile_llm(profile_name: str, config: Config) -> LLM:
    try:
        return LLMProfileStore().load(profile_name, cipher=config.cipher)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{profile_name}' not found",
        )
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Profile store is busy. Please retry.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _append_system_suffix(existing: str | None, system_text: str) -> str:
    existing_text = (existing or "").strip()
    system_text = system_text.strip()
    if not existing_text:
        return system_text
    if not system_text:
        return existing_text
    return f"{existing_text}\n\n{system_text}"


def _with_profile_llm_and_system_text(
    agent_settings: OpenHandsAgentSettings | ACPAgentSettings,
    llm: LLM,
    system_text: str,
) -> OpenHandsAgentSettings | ACPAgentSettings:
    updated = agent_settings.model_copy(update={"llm": llm})
    if not system_text:
        return updated

    if isinstance(updated, OpenHandsAgentSettings):
        context = updated.agent_context
        suffix = _append_system_suffix(context.system_message_suffix, system_text)
        return updated.model_copy(
            update={
                "agent_context": context.model_copy(
                    update={"system_message_suffix": suffix}
                )
            }
        )

    context = updated.agent_context or AgentContext()
    suffix = _append_system_suffix(context.system_message_suffix, system_text)
    return updated.model_copy(
        update={
            "agent_context": context.model_copy(
                update={"system_message_suffix": suffix}
            )
        }
    )


def _content_to_sdk_parts(
    message: OpenAIChatMessage,
) -> list[TextContent | ImageContent]:
    content = message.content
    if content is None:
        return []
    if isinstance(content, str):
        return [TextContent(text=content)]

    parts: list[TextContent | ImageContent] = []
    for part in content:
        if part.type == "text":
            if part.text:
                parts.append(TextContent(text=part.text))
            continue
        if part.type == "image_url":
            if isinstance(part.image_url, str):
                image_url = part.image_url
            elif part.image_url is not None:
                image_url = part.image_url.url
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="image_url content part is missing a url",
                )
            parts.append(ImageContent(image_urls=[image_url]))
            continue
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported content part type: {part.type}",
        )
    return parts


def _message_text(message: OpenAIChatMessage) -> str:
    text_parts: list[str] = []
    for part in _content_to_sdk_parts(message):
        if isinstance(part, TextContent):
            text_parts.append(part.text)
    return "\n".join(text_parts)


def _latest_user_message(messages: list[OpenAIChatMessage]) -> OpenAIChatMessage:
    for message in reversed(messages):
        if message.role == "user":
            return message
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="At least one user message is required",
    )


def _system_text(messages: list[OpenAIChatMessage]) -> str:
    text_parts: list[str] = []
    for message in messages:
        if message.role != "system":
            continue
        text = _message_text(message)
        if text:
            text_parts.append(text)
    return "\n\n".join(text_parts)


def _conversation_request(
    *,
    request: OpenAIChatCompletionRequest,
    config: Config,
    conversation_id: UUID | None,
) -> StartConversationRequest:
    profile_name = _profile_name_from_model(request.model)
    llm = _load_profile_llm(profile_name, config)
    settings = get_settings_store(config).load() or PersistedSettings()
    agent_settings = _with_profile_llm_and_system_text(
        settings.agent_settings,
        llm,
        _system_text(request.messages),
    )
    user_message = _latest_user_message(request.messages)
    conversation_settings = settings.conversation_settings.model_copy(
        update={"agent_settings": agent_settings}
    )
    return conversation_settings.create_request(
        StartConversationRequest,
        workspace=LocalWorkspace(working_dir=_DEFAULT_WORKSPACE),
        conversation_id=conversation_id,
        initial_message=SendMessageRequest(
            role="user",
            content=_content_to_sdk_parts(user_message),
            run=True,
        ),
        autotitle=False,
    )


async def _wait_for_completion(
    event_service: EventService,
    *,
    allow_existing_response: bool,
    min_event_count: int | None = None,
    timeout_seconds: float = _GATEWAY_TIMEOUT_SECONDS,
) -> ConversationExecutionStatus:
    deadline = time.monotonic() + timeout_seconds
    observed_run = False
    last_status = ConversationExecutionStatus.IDLE

    while True:
        state = await event_service.get_state()
        last_status = state.execution_status
        enough_new_events = (
            min_event_count is None or len(state.events) > min_event_count
        )
        if last_status == ConversationExecutionStatus.RUNNING:
            observed_run = True
        elif last_status.is_terminal() and (
            allow_existing_response or observed_run or enough_new_events
        ):
            return last_status
        elif observed_run and enough_new_events:
            return last_status
        elif (
            allow_existing_response
            and enough_new_events
            and await event_service.get_agent_final_response()
        ):
            return last_status

        if time.monotonic() >= deadline:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Agent run timed out",
            )
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def _raise_for_terminal_error(status_value: ConversationExecutionStatus) -> None:
    if status_value in (
        ConversationExecutionStatus.ERROR,
        ConversationExecutionStatus.STUCK,
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent run ended with status: {status_value.value}",
        )
    if status_value in (
        ConversationExecutionStatus.PAUSED,
        ConversationExecutionStatus.WAITING_FOR_CONFIRMATION,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent run ended with status: {status_value.value}",
        )


def _openai_usage_from_state(state: ConversationState) -> OpenAIUsage:
    token_usage = state.stats.get_combined_metrics().accumulated_token_usage
    if token_usage is None:
        return OpenAIUsage(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

    prompt_tokens = token_usage.prompt_tokens
    completion_tokens = token_usage.completion_tokens
    return OpenAIUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


async def list_openai_models() -> OpenAIModelListResponse:
    try:
        profiles = LLMProfileStore().list_summaries()
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Profile store is busy. Please retry.",
        )
    data = [
        OpenAIModel(
            id=f"{_MODEL_PREFIX}{profile['name']}",
            object="model",
            created=0,
            owned_by="openhands",
        )
        for profile in profiles
        if isinstance(profile.get("name"), str)
    ]
    data.sort(key=lambda model: model.id)
    return OpenAIModelListResponse(data=data)


async def run_chat_completion(
    *,
    request: OpenAIChatCompletionRequest,
    config: Config,
    conversation_service: ConversationService,
    reusable_conversation_id: UUID | None,
) -> OpenAIChatCompletionResult:
    if request.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Streaming chat completions are not supported yet",
        )

    start_request = _conversation_request(
        request=request,
        config=config,
        conversation_id=reusable_conversation_id,
    )
    event_service = None
    conversation_id = reusable_conversation_id
    min_event_count: int | None = None

    if reusable_conversation_id is not None:
        event_service = await conversation_service.get_event_service(
            reusable_conversation_id
        )
        if event_service is not None:
            min_event_count = len((await event_service.get_state()).events) + 1
            user_message = _latest_user_message(request.messages)
            await event_service.send_message(
                Message(role="user", content=_content_to_sdk_parts(user_message)),
                run=True,
            )
    allow_existing_response = event_service is None

    if event_service is None:
        conversation_info, _ = await conversation_service.start_conversation(
            start_request
        )
        conversation_id = conversation_info.id
        event_service = await conversation_service.get_event_service(
            conversation_info.id
        )
        if event_service is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Conversation did not start",
            )

    status_value = await _wait_for_completion(
        event_service,
        allow_existing_response=allow_existing_response,
        min_event_count=min_event_count,
    )
    _raise_for_terminal_error(status_value)
    state = await event_service.get_state()
    final_response = await event_service.get_agent_final_response()
    response = OpenAIChatCompletionResponse(
        id=f"chatcmpl-{uuid4().hex}",
        object="chat.completion",
        created=int(time.time()),
        model=request.model,
        choices=[
            OpenAIChatCompletionChoice(
                index=0,
                finish_reason="stop",
                message=OpenAIResponseMessage(
                    role="assistant",
                    content=final_response,
                ),
            )
        ],
        usage=_openai_usage_from_state(state),
    )
    assert conversation_id is not None
    return OpenAIChatCompletionResult(
        response=response, conversation_id=conversation_id
    )
