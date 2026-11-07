"""OpenAI-compatible gateway routes for the agent server."""

import json
from hashlib import sha256
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import TypeAdapter, ValidationError

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry
from openhands.agent_server.openai.idempotency import Claim, GatewayIdempotencyStore
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
    prepare_chat_completion,
    prepare_response,
    run_chat_completion,
    run_response,
)
from openhands.sdk.conversation.types import (
    ConversationObservabilityMetadata,
    ConversationObservabilitySpanName,
    ConversationObservabilityTags,
)


openai_router = APIRouter(tags=["OpenAI Compatibility"])

_SESSION_API_KEY_HEADER = APIKeyHeader(name="X-Session-API-Key", auto_error=False)
_AUTHORIZATION_HEADER = HTTPBearer(auto_error=False)
_OBSERVABILITY_SPAN_NAME_ADAPTER = TypeAdapter(ConversationObservabilitySpanName)
_OBSERVABILITY_TAGS_ADAPTER = TypeAdapter(ConversationObservabilityTags)
_OBSERVABILITY_METADATA_ADAPTER = TypeAdapter(ConversationObservabilityMetadata)


def check_openai_api_key(
    request: Request,
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

    Reads config from ``request.app.state`` at request time so that keys
    delivered via ``POST /api/init`` take effect immediately.
    """
    config: Config = request.app.state.config
    if not config.session_api_keys:
        return
    bearer_token = authorization.credentials if authorization else None
    if session_api_key in config.session_api_keys:
        return
    if bearer_token in config.session_api_keys:
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED)


def _get_config(request: Request) -> Config:
    config = getattr(request.app.state, "config", None)
    if not isinstance(config, Config):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent server config is not available",
        )
    return config


def _get_docker_registry(request: Request) -> DockerConversationRegistry | None:
    registry = getattr(request.app.state, "conversation_registry", None)
    return registry if isinstance(registry, DockerConversationRegistry) else None


async def _claim_request(
    request: Request, config: Config, conversation_id: UUID | None
) -> tuple[GatewayIdempotencyStore, Claim] | None:
    key = request.headers.get("Idempotency-Key")
    if key is None:
        return None
    if not key.strip() or len(key.encode()) > 255:
        raise HTTPException(400, "Idempotency-Key must contain 1 to 255 bytes")
    effective_headers = [
        request.url.path,
        request.url.query,
        request.headers.get("X-OpenHands-ServerConversation-ID"),
        request.headers.get("X-OpenHands-Observability-Span-Name"),
        request.headers.get("X-OpenHands-Observability-Tags"),
        request.headers.get("X-OpenHands-Observability-Metadata"),
        request.headers.get("X-OpenHands-Observability-Parent-Span-Context"),
    ]
    identity = json.dumps(effective_headers, separators=(",", ":")).encode()
    request_digest = sha256(identity + b"\0" + await request.body()).hexdigest()
    store = GatewayIdempotencyStore(config)
    claim = await store.claim(
        key=key,
        request_digest=request_digest,
        conversation_id=conversation_id or uuid4(),
    )
    return store, claim


def _claim_store_error(conversation_id: UUID) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "Idempotency result is unavailable. Do not retry this key."},
        headers={
            "X-OpenHands-ServerConversation-ID": str(conversation_id),
            "X-Should-Retry": "false",
        },
    )


def _existing_claim_response(
    claim: Claim, *, stream: bool = False, include_usage: bool = False
) -> JSONResponse | StreamingResponse:
    headers = {"X-Should-Retry": "false"}
    if claim.state == "pending":
        headers["X-OpenHands-ServerConversation-ID"] = str(claim.conversation_id)
        return JSONResponse(
            status_code=409,
            content={"detail": "Request is in progress or its outcome is unknown"},
            headers=headers,
        )
    if claim.state == "mismatch":
        return JSONResponse(
            status_code=409,
            content={"detail": "Idempotency-Key was used for a different request"},
            headers=headers,
        )
    if claim.state == "full":
        return JSONResponse(
            status_code=503,
            content={"detail": "Gateway idempotency store is full"},
            headers=headers,
        )
    assert claim.state == "complete" and claim.response is not None
    headers["X-OpenHands-ServerConversation-ID"] = str(claim.conversation_id)
    if stream:
        response = OpenAIChatCompletionResponse.model_validate(claim.response)
        return StreamingResponse(
            iter_openai_chat_completion_sse(response, include_usage=include_usage),
            media_type="text/event-stream",
            headers=headers,
        )
    return JSONResponse(content=claim.response, headers=headers)


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
        raise HTTPException(
            status_code=422,
            detail="X-OpenHands-Observability-Metadata must be a JSON object",
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False),
        ) from exc
    if parent_span_context:
        overrides["observability_parent_span_context"] = parent_span_context
    return overrides


@openai_router.get("/v1/models", response_model=OpenAIModelListResponse)
async def get_openai_models(request: Request) -> OpenAIModelListResponse:
    _get_config(request)
    return await list_openai_models()


@openai_router.post(
    "/v1/chat/completions",
    response_model=OpenAIChatCompletionResponse,
    response_model_exclude_none=True,
)
async def create_chat_completion(
    body: OpenAIChatCompletionRequest,
    request: Request,
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
) -> OpenAIChatCompletionResponse | JSONResponse | StreamingResponse:
    config = _get_config(request)
    overrides = _parse_observability_overrides(
        span_name=x_openhands_observability_span_name,
        tags=x_openhands_observability_tags,
        metadata=x_openhands_observability_metadata,
        parent_span_context=x_openhands_observability_parent_span_context,
    )
    conversation_id = x_openhands_server_conversation_id or uuid4()
    keyed = await _claim_request(request, config, conversation_id)
    if keyed is not None and keyed[1].state != "new":
        return _existing_claim_response(
            keyed[1],
            stream=body.stream,
            include_usage=bool(
                body.stream_options and body.stream_options.include_usage
            ),
        )
    prepared = None
    if keyed is not None:
        try:
            prepared = prepare_chat_completion(body, config, conversation_id)
        except Exception:
            assert keyed[1].claim_id is not None
            try:
                await keyed[0].release(claim_id=keyed[1].claim_id)
            except Exception:
                return _claim_store_error(conversation_id)
            raise
    try:
        result = await run_chat_completion(
            request=body.model_copy(update={"stream": False}) if body.stream else body,
            config=config,
            conversation_service=conversation_service,
            reusable_conversation_id=(
                keyed[1].conversation_id
                if keyed is not None
                else x_openhands_server_conversation_id
            ),
            registry=_get_docker_registry(request),
            observability_overrides=overrides,
            prepared=prepared,
        )
    except HTTPException as exc:
        if keyed is not None and exc.status_code == 429:
            assert keyed[1].claim_id is not None
            try:
                await keyed[0].release(claim_id=keyed[1].claim_id)
            except Exception:
                return _claim_store_error(keyed[1].conversation_id)
        raise
    if keyed is not None:
        assert keyed[1].claim_id is not None
        try:
            await keyed[0].complete(
                claim_id=keyed[1].claim_id,
                response=result.response.model_dump(mode="json", exclude_none=True),
            )
        except Exception:
            return _claim_store_error(result.conversation_id)
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
    request: Request,
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
) -> OpenAIResponse | JSONResponse | StreamingResponse:
    config = _get_config(request)
    overrides = _parse_observability_overrides(
        span_name=x_openhands_observability_span_name,
        tags=x_openhands_observability_tags,
        metadata=x_openhands_observability_metadata,
        parent_span_context=x_openhands_observability_parent_span_context,
    )
    conversation_id = uuid4()
    keyed = await _claim_request(request, config, conversation_id)
    if keyed is not None and keyed[1].state != "new":
        return _existing_claim_response(keyed[1])
    prepared = None
    if keyed is not None:
        try:
            prepared = prepare_response(body, config, conversation_id)
        except Exception:
            assert keyed[1].claim_id is not None
            try:
                await keyed[0].release(claim_id=keyed[1].claim_id)
            except Exception:
                return _claim_store_error(conversation_id)
            raise
    try:
        result = await run_response(
            request=body,
            config=config,
            conversation_service=conversation_service,
            registry=_get_docker_registry(request),
            conversation_id=keyed[1].conversation_id if keyed is not None else None,
            observability_overrides=overrides,
            prepared=prepared,
        )
    except HTTPException as exc:
        if keyed is not None and exc.status_code == 429:
            assert keyed[1].claim_id is not None
            try:
                await keyed[0].release(claim_id=keyed[1].claim_id)
            except Exception:
                return _claim_store_error(keyed[1].conversation_id)
        raise
    if keyed is not None:
        assert keyed[1].claim_id is not None
        try:
            await keyed[0].complete(
                claim_id=keyed[1].claim_id,
                response=result.response.model_dump(mode="json", exclude_none=True),
            )
        except Exception:
            return _claim_store_error(result.conversation_id)
    response.headers["X-OpenHands-ServerConversation-ID"] = str(result.conversation_id)
    return result.response
