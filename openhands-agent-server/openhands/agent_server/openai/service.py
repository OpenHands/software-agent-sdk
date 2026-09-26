"""Service logic for the OpenAI-compatible agent-server gateway."""

import asyncio
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.openai.models import (
    OpenAIChatCompletionChoice,
    OpenAIChatCompletionChunk,
    OpenAIChatCompletionChunkChoice,
    OpenAIChatCompletionChunkChoiceDelta,
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIChatMessage,
    OpenAIModel,
    OpenAIModelListResponse,
    OpenAIResponse,
    OpenAIResponseInputMessage,
    OpenAIResponseInputTokensDetails,
    OpenAIResponseMessage,
    OpenAIResponseOutputMessage,
    OpenAIResponseOutputText,
    OpenAIResponseOutputTokensDetails,
    OpenAIResponseRequest,
    OpenAIResponseUsage,
    OpenAIUsage,
)
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_llm_profile_store,
    get_settings_store,
)
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
from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.sdk.settings import ACPAgentSettings, OpenHandsAgentSettings
from openhands.sdk.workspace import LocalWorkspace


_MODEL_PREFIX = "openhands_"
_RESPONSE_ID_PREFIX = "resp_"
# Fixed gateway defaults are sufficient for the initial local-first endpoint;
# promote them to Config only if clients need deployment-specific tuning.
_GATEWAY_TIMEOUT_SECONDS = 120.0
_POLL_INTERVAL_SECONDS = 2


@dataclass(frozen=True)
class OpenAIChatCompletionResult:
    response: OpenAIChatCompletionResponse
    conversation_id: UUID


@dataclass(frozen=True)
class OpenAIResponseResult:
    response: OpenAIResponse
    conversation_id: UUID


@dataclass(frozen=True)
class OpenAIAgentRunResult:
    conversation_id: UUID
    final_response: str
    state: ConversationState


def _profile_name_from_model(model: str) -> str:
    if model.startswith(_MODEL_PREFIX) and len(model) > len(_MODEL_PREFIX):
        return model[len(_MODEL_PREFIX) :]
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Unknown OpenHands model '{model}'. Use GET /v1/models.",
    )


def _load_profile_llm(profile_name: str, config: Config) -> LLM:
    try:
        return get_llm_profile_store().load(profile_name, cipher=config.cipher)
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
    return "\n\n".join(
        text for text in ((existing or "").strip(), system_text.strip()) if text
    )


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
        if message.role not in {"system", "developer"}:
            continue
        text = _message_text(message)
        if text:
            text_parts.append(text)
    return "\n\n".join(text_parts)


def _response_content_to_sdk_parts(
    message: OpenAIResponseInputMessage,
) -> list[TextContent | ImageContent]:
    if isinstance(message.content, str):
        return [TextContent(text=message.content)]

    parts: list[TextContent | ImageContent] = []
    for part in message.content:
        if part.type in {"input_text", "output_text"}:
            if part.text:
                parts.append(TextContent(text=part.text))
            continue
        if part.type == "input_image":
            if not part.image_url:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="input_image content part is missing an image_url",
                )
            parts.append(ImageContent(image_urls=[part.image_url]))
            continue
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported Responses content part type: {part.type}",
        )
    return parts


def _response_message_text(message: OpenAIResponseInputMessage) -> str:
    return "\n".join(
        part.text
        for part in _response_content_to_sdk_parts(message)
        if isinstance(part, TextContent)
    )


def _response_system_text(request: OpenAIResponseRequest) -> str:
    text_parts = [request.instructions.strip()] if request.instructions else []
    if isinstance(request.input, str):
        return "\n\n".join(text for text in text_parts if text)

    for message in request.input:
        if message.role not in {"system", "developer"}:
            continue
        text = _response_message_text(message)
        if text:
            text_parts.append(text)
    return "\n\n".join(text for text in text_parts if text)


def _response_user_content(
    request: OpenAIResponseRequest,
) -> list[TextContent | ImageContent]:
    if isinstance(request.input, str):
        if not request.input:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Responses input must not be empty",
            )
        return [TextContent(text=request.input)]

    messages = [
        message
        for message in request.input
        if message.role not in {"system", "developer"}
    ]
    if not any(message.role == "user" for message in messages):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Responses input must include a user message",
        )
    if len(messages) == 1 and messages[0].role == "user":
        return _response_content_to_sdk_parts(messages[0])

    parts: list[TextContent | ImageContent] = []
    for message in messages:
        parts.append(TextContent(text=f'<message role="{message.role}">'))
        parts.extend(_response_content_to_sdk_parts(message))
        parts.append(TextContent(text="</message>"))
    return parts


def _create_conversation_request(
    *,
    model: str,
    system_text: str,
    user_content: list[TextContent | ImageContent],
    config: Config,
    conversation_id: UUID | None,
) -> StartConversationRequest:
    profile_name = _profile_name_from_model(model)
    llm = _load_profile_llm(profile_name, config)
    settings = get_settings_store(config).load() or PersistedSettings()
    agent_settings = _with_profile_llm_and_system_text(
        settings.agent_settings,
        llm,
        system_text,
    )
    conversation_settings = settings.conversation_settings.model_copy(
        update={"agent_settings": agent_settings}
    )
    return conversation_settings.create_request(
        StartConversationRequest,
        workspace=LocalWorkspace(working_dir=config.workspace_path),
        conversation_id=conversation_id,
        initial_message=SendMessageRequest(
            role="user",
            content=user_content,
            run=True,
        ),
        autotitle=False,
    )


def _conversation_request(
    *,
    request: OpenAIChatCompletionRequest,
    config: Config,
    conversation_id: UUID | None,
) -> StartConversationRequest:
    user_message = _latest_user_message(request.messages)
    return _create_conversation_request(
        model=request.model,
        system_text=_system_text(request.messages),
        user_content=_content_to_sdk_parts(user_message),
        config=config,
        conversation_id=conversation_id,
    )


def _response_conversation_request(
    *,
    request: OpenAIResponseRequest,
    user_content: list[TextContent | ImageContent],
    config: Config,
    conversation_id: UUID | None,
) -> StartConversationRequest:
    return _create_conversation_request(
        model=request.model,
        system_text=_response_system_text(request),
        user_content=user_content,
        config=config,
        conversation_id=conversation_id,
    )


def _response_id() -> str:
    return f"{_RESPONSE_ID_PREFIX}{uuid4().hex}"


# Reused Chat Completions conversations do not expose a request-specific run
# task, so wait for both new events and a terminal state. Fresh conversations
# use EventService.wait_for_run_completion() below.
async def _wait_for_reused_conversation_completion(
    event_service: EventService,
    *,
    min_event_count: int,
    timeout_seconds: float = _GATEWAY_TIMEOUT_SECONDS,
) -> ConversationExecutionStatus:
    deadline = time.monotonic() + timeout_seconds
    observed_run = False
    last_status = ConversationExecutionStatus.IDLE

    while True:
        state = await event_service.get_state()
        last_status = state.execution_status
        enough_new_events = len(state.events) > min_event_count
        if last_status == ConversationExecutionStatus.RUNNING:
            observed_run = True
        elif last_status.is_terminal() and (observed_run or enough_new_events):
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
    if status_value != ConversationExecutionStatus.FINISHED:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent run ended with unexpected status: {status_value.value}",
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


def _openai_response_usage_from_state(state: ConversationState) -> OpenAIResponseUsage:
    token_usage = state.stats.get_combined_metrics().accumulated_token_usage
    if token_usage is None:
        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        reasoning_tokens = 0
    else:
        prompt_tokens = token_usage.prompt_tokens
        completion_tokens = token_usage.completion_tokens
        cached_tokens = token_usage.cache_read_tokens
        reasoning_tokens = token_usage.reasoning_tokens
    return OpenAIResponseUsage(
        input_tokens=prompt_tokens,
        input_tokens_details=OpenAIResponseInputTokensDetails(
            cached_tokens=cached_tokens
        ),
        output_tokens=completion_tokens,
        output_tokens_details=OpenAIResponseOutputTokensDetails(
            reasoning_tokens=reasoning_tokens
        ),
        total_tokens=prompt_tokens + completion_tokens,
    )


def _openai_stream_event(payload: OpenAIChatCompletionChunk) -> str:
    data = payload.model_dump(mode="json", exclude_none=True)
    return f"data: {json.dumps(data, separators=(',', ':'))}\n\n"


def iter_openai_chat_completion_sse(
    response: OpenAIChatCompletionResponse,
    *,
    include_usage: bool,
) -> Iterator[str]:
    created = int(response.created)
    completion_id = response.id
    model = response.model
    content = response.choices[0].message.content
    finish_reason = response.choices[0].finish_reason

    yield _openai_stream_event(
        OpenAIChatCompletionChunk(
            id=completion_id,
            object="chat.completion.chunk",
            created=created,
            model=model,
            choices=[
                OpenAIChatCompletionChunkChoice(
                    index=0,
                    delta=OpenAIChatCompletionChunkChoiceDelta(role="assistant"),
                    finish_reason=None,
                )
            ],
        )
    )
    yield _openai_stream_event(
        OpenAIChatCompletionChunk(
            id=completion_id,
            object="chat.completion.chunk",
            created=created,
            model=model,
            choices=[
                OpenAIChatCompletionChunkChoice(
                    index=0,
                    delta=OpenAIChatCompletionChunkChoiceDelta(content=content),
                    finish_reason=None,
                )
            ],
        )
    )
    yield _openai_stream_event(
        OpenAIChatCompletionChunk(
            id=completion_id,
            object="chat.completion.chunk",
            created=created,
            model=model,
            choices=[
                OpenAIChatCompletionChunkChoice(
                    index=0,
                    delta=OpenAIChatCompletionChunkChoiceDelta(),
                    finish_reason=finish_reason,
                )
            ],
        )
    )
    if include_usage:
        yield _openai_stream_event(
            OpenAIChatCompletionChunk(
                id=completion_id,
                object="chat.completion.chunk",
                created=created,
                model=model,
                choices=[],
                usage=response.usage,
            )
        )
    yield "data: [DONE]\n\n"


async def list_openai_models() -> OpenAIModelListResponse:
    try:
        profiles = get_llm_profile_store().list_summaries()
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


async def _run_agent(
    *,
    start_request: StartConversationRequest,
    user_content: list[TextContent | ImageContent],
    conversation_service: ConversationService,
    reusable_conversation_id: UUID | None,
) -> OpenAIAgentRunResult:
    event_service = None
    conversation_id = reusable_conversation_id
    min_event_count: int | None = None

    if reusable_conversation_id is not None:
        event_service = await conversation_service.get_event_service(
            reusable_conversation_id
        )
        if event_service is not None:
            state_before = await event_service.get_state()
            min_event_count = len(state_before.events) + 1
            await event_service.send_message(
                Message(role="user", content=user_content),
                run=True,
            )
    started_new_conversation = event_service is None

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

    if started_new_conversation:
        try:
            status_value = await event_service.wait_for_run_completion(
                timeout=_GATEWAY_TIMEOUT_SECONDS
            )
        except TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Agent run timed out",
            ) from None
    else:
        assert min_event_count is not None
        status_value = await _wait_for_reused_conversation_completion(
            event_service,
            min_event_count=min_event_count,
        )
    _raise_for_terminal_error(status_value)
    state = await event_service.get_state()
    final_response = await event_service.get_agent_final_response()
    assert conversation_id is not None
    return OpenAIAgentRunResult(
        conversation_id=conversation_id,
        final_response=final_response,
        state=state,
    )


async def run_chat_completion(
    *,
    request: OpenAIChatCompletionRequest,
    config: Config,
    conversation_service: ConversationService,
    reusable_conversation_id: UUID | None,
    observability_overrides: dict[str, object] | None = None,
) -> OpenAIChatCompletionResult:
    if request.stream:
        # SSE streaming needs incremental agent-event forwarding; add it separately.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Streaming chat completions are not supported yet",
        )

    start_request = _conversation_request(
        request=request,
        config=config,
        conversation_id=reusable_conversation_id,
    )
    if observability_overrides:
        start_request = start_request.model_copy(update=observability_overrides)
    user_message = _latest_user_message(request.messages)
    agent_result = await _run_agent(
        start_request=start_request,
        user_content=_content_to_sdk_parts(user_message),
        conversation_service=conversation_service,
        reusable_conversation_id=reusable_conversation_id,
    )
    # EventService.get_agent_final_response() returns final text from the SDK's
    # get_agent_final_response(), so the gateway emits assistant text only.
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
                    content=agent_result.final_response,
                ),
            )
        ],
        usage=_openai_usage_from_state(agent_result.state),
    )
    return OpenAIChatCompletionResult(
        response=response,
        conversation_id=agent_result.conversation_id,
    )


async def run_response(
    *,
    request: OpenAIResponseRequest,
    config: Config,
    conversation_service: ConversationService,
    observability_overrides: dict[str, object] | None = None,
) -> OpenAIResponseResult:
    if request.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Streaming responses are not supported yet",
        )
    if request.store:
        # `store=True` promises server-side persistence retrievable through
        # GET /v1/responses/{id}, which this gateway does not provide. Rejecting
        # it is safer than silently returning a response no client can fetch.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Persistent response storage (store=True) is not supported yet",
        )
    if request.previous_response_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="previous_response_id is not supported; replay input items instead",
        )

    user_content = _response_user_content(request)
    start_request = _response_conversation_request(
        request=request,
        user_content=user_content,
        config=config,
        conversation_id=None,
    )
    if observability_overrides:
        start_request = start_request.model_copy(update=observability_overrides)
    created_at = time.time()
    agent_result = await _run_agent(
        start_request=start_request,
        user_content=user_content,
        conversation_service=conversation_service,
        reusable_conversation_id=None,
    )
    completed_at = time.time()
    response = OpenAIResponse(
        id=_response_id(),
        created_at=created_at,
        completed_at=completed_at,
        instructions=request.instructions,
        metadata=request.metadata,
        model=request.model,
        object="response",
        output=[
            OpenAIResponseOutputMessage(
                id=f"msg_{uuid4().hex}",
                content=[
                    OpenAIResponseOutputText(
                        annotations=[],
                        text=agent_result.final_response,
                        type="output_text",
                    )
                ],
                role="assistant",
                status="completed",
                type="message",
            )
        ],
        parallel_tool_calls=False,
        previous_response_id=None,
        status="completed",
        tool_choice="none",
        tools=[],
        usage=_openai_response_usage_from_state(agent_result.state),
    )
    return OpenAIResponseResult(
        response=response,
        conversation_id=agent_result.conversation_id,
    )
