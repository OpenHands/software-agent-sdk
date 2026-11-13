"""OpenAI-compatible gateway routes for the agent server."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import TypeAdapter, ValidationError

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_registry import ConversationRegistry
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import (
    get_config,
    get_conversation_registry,
    get_conversation_service,
    get_stores,
)
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry
from openhands.agent_server.openai.models import (
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIModelListResponse,
    OpenAIResponse,
    OpenAIResponseRequest,
)
from openhands.agent_server.openai.service import (
    iter_openai_chat_completion_sse,
    list_openai_models,
    run_chat_completion,
    run_response,
)
from openhands.agent_server.persistence import ServerStores
from openhands.sdk.conversation.types import (
    ConversationObservabilityMetadata,
    ConversationObservabilitySpanName,
    ConversationObservabilityTags,
)
from openhands.sdk.errors import Unauthenticated, Unprocessable


openai_router = APIRouter(tags=["OpenAI Compatibility"])

_SESSION_API_KEY_HEADER = APIKeyHeader(name="X-Session-API-Key", auto_error=False)
_AUTHORIZATION_HEADER = HTTPBearer(auto_error=False)
_OBSERVABILITY_SPAN_NAME_ADAPTER = TypeAdapter(ConversationObservabilitySpanName)
_OBSERVABILITY_TAGS_ADAPTER = TypeAdapter(ConversationObservabilityTags)
_OBSERVABILITY_METADATA_ADAPTER = TypeAdapter(ConversationObservabilityMetadata)


def check_openai_api_key(
    config: Config = Depends(get_config),
    session_api_key: str | None = Depends(_SESSION_API_KEY_HEADER),
    authorization: HTTPAuthorizationCredentials | None = Depends(_AUTHORIZATION_HEADER),
) -> None:
    """Accept the same session key through OpenHands and OpenAI auth shapes.

    ``X-Session-API-Key`` preserves compatibility with existing agent-server
    clients, while ``Authorization: Bearer`` lets OpenAI-compatible clients use
    their standard API-key header. Both forms validate against
    ``config.session_api_keys``; this does not introduce a second credential
    system. When no session keys are configured, the local server remains
    unauthenticated like the existing agent-server API.

    Reads the config at request time so that keys delivered via
    ``POST /api/init`` take effect immediately.
    """
    if not config.session_api_keys:
        return
    bearer_token = authorization.credentials if authorization else None
    if session_api_key in config.session_api_keys:
        return
    if bearer_token in config.session_api_keys:
        return
    raise Unauthenticated()


def _get_docker_registry(
    registry: ConversationRegistry,
) -> DockerConversationRegistry | None:
    return registry if isinstance(registry, DockerConversationRegistry) else None


def _parse_observability_overrides(
    *,
    span_name: str | None,
    tags: str | None,
    metadata: str | None,
    parent_span_context: str | None = None,
) -> dict[str, object]:
    overrides: dict[str, object] = {}
    try:
        if span_name:
            overrides["observability_span_name"] = (
                _OBSERVABILITY_SPAN_NAME_ADAPTER.validate_python(span_name)
            )
        if tags:
            tag_values = [tag.strip() for tag in tags.split(",") if tag.strip()]
            overrides["observability_tags"] = (
                _OBSERVABILITY_TAGS_ADAPTER.validate_python(tag_values)
            )
        if metadata:
            metadata_payload = json.loads(metadata)
            overrides["observability_metadata"] = (
                _OBSERVABILITY_METADATA_ADAPTER.validate_python(metadata_payload)
            )
    except json.JSONDecodeError as exc:
        raise Unprocessable(
            "X-OpenHands-Observability-Metadata must be a JSON object"
        ) from exc
    except ValidationError as exc:
        raise Unprocessable(exc.errors(include_context=False)) from exc
    if parent_span_context:
        overrides["observability_parent_span_context"] = parent_span_context
    return overrides


@openai_router.get("/v1/models", response_model=OpenAIModelListResponse)
async def get_openai_models(
    stores: ServerStores = Depends(get_stores),
) -> OpenAIModelListResponse:
    return await list_openai_models(stores)


@openai_router.post(
    "/v1/chat/completions",
    response_model=OpenAIChatCompletionResponse,
    response_model_exclude_none=True,
)
async def create_chat_completion(
    body: OpenAIChatCompletionRequest,
    response: Response,
    x_openhands_server_conversation_id: Annotated[
        UUID | None, Header(alias="X-OpenHands-ServerConversation-ID")
    ] = None,
    x_openhands_observability_span_name: Annotated[
        str | None, Header(alias="X-OpenHands-Observability-Span-Name")
    ] = None,
    x_openhands_observability_tags: Annotated[
        str | None, Header(alias="X-OpenHands-Observability-Tags")
    ] = None,
    x_openhands_observability_metadata: Annotated[
        str | None, Header(alias="X-OpenHands-Observability-Metadata")
    ] = None,
    x_openhands_observability_parent_span_context: Annotated[
        str | None, Header(alias="X-OpenHands-Observability-Parent-Span-Context")
    ] = None,
    conversation_service: ConversationService = Depends(get_conversation_service),
    config: Config = Depends(get_config),
    stores: ServerStores = Depends(get_stores),
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
) -> OpenAIChatCompletionResponse | StreamingResponse:
    result = await run_chat_completion(
        request=body.model_copy(update={"stream": False}) if body.stream else body,
        config=config,
        stores=stores,
        conversation_service=conversation_service,
        reusable_conversation_id=x_openhands_server_conversation_id,
        registry=_get_docker_registry(conversation_registry),
        observability_overrides=_parse_observability_overrides(
            span_name=x_openhands_observability_span_name,
            tags=x_openhands_observability_tags,
            metadata=x_openhands_observability_metadata,
            parent_span_context=x_openhands_observability_parent_span_context,
        ),
    )
    conversation_id = str(result.conversation_id)
    if body.stream:
        include_usage = (
            body.stream_options is not None and body.stream_options.include_usage
        )
        return StreamingResponse(
            iter_openai_chat_completion_sse(
                result.response,
                include_usage=include_usage,
            ),
            media_type="text/event-stream",
            headers={"X-OpenHands-ServerConversation-ID": conversation_id},
        )

    response.headers["X-OpenHands-ServerConversation-ID"] = conversation_id
    return result.response


@openai_router.post(
    "/v1/responses",
    response_model=OpenAIResponse,
    response_model_exclude_none=True,
)
async def create_response(
    body: OpenAIResponseRequest,
    response: Response,
    x_openhands_observability_span_name: Annotated[
        str | None, Header(alias="X-OpenHands-Observability-Span-Name")
    ] = None,
    x_openhands_observability_tags: Annotated[
        str | None, Header(alias="X-OpenHands-Observability-Tags")
    ] = None,
    x_openhands_observability_metadata: Annotated[
        str | None, Header(alias="X-OpenHands-Observability-Metadata")
    ] = None,
    x_openhands_observability_parent_span_context: Annotated[
        str | None, Header(alias="X-OpenHands-Observability-Parent-Span-Context")
    ] = None,
    conversation_service: ConversationService = Depends(get_conversation_service),
    config: Config = Depends(get_config),
    stores: ServerStores = Depends(get_stores),
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
) -> OpenAIResponse:
    result = await run_response(
        request=body,
        config=config,
        stores=stores,
        conversation_service=conversation_service,
        registry=_get_docker_registry(conversation_registry),
        observability_overrides=_parse_observability_overrides(
            span_name=x_openhands_observability_span_name,
            tags=x_openhands_observability_tags,
            metadata=x_openhands_observability_metadata,
            parent_span_context=x_openhands_observability_parent_span_context,
        ),
    )
    response.headers["X-OpenHands-ServerConversation-ID"] = str(result.conversation_id)
    return result.response
