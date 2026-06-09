"""OpenAI-compatible gateway routes for the agent server."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.openai_models import (
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIModelListResponse,
)
from openhands.agent_server.openai_service import (
    list_openai_models,
    run_chat_completion,
)


openai_router = APIRouter(tags=["OpenAI Compatibility"])

_SESSION_API_KEY_HEADER = APIKeyHeader(name="X-Session-API-Key", auto_error=False)
_AUTHORIZATION_HEADER = HTTPBearer(auto_error=False)


def create_openai_api_key_dependency(config: Config):
    """Accept the same session key through OpenHands and OpenAI auth shapes.

    ``X-Session-API-Key`` preserves compatibility with existing agent-server
    clients, while ``Authorization: Bearer`` lets OpenAI-compatible clients use
    their standard API-key header. Both forms validate against
    ``config.session_api_keys``; this does not introduce a second credential
    system. When no session keys are configured, the local server remains
    unauthenticated like the existing agent-server API.
    """

    def check_openai_api_key(
        session_api_key: str | None = Depends(_SESSION_API_KEY_HEADER),
        authorization: HTTPAuthorizationCredentials | None = Depends(
            _AUTHORIZATION_HEADER
        ),
    ) -> None:
        if not config.session_api_keys:
            return
        bearer_token = authorization.credentials if authorization else None
        if session_api_key in config.session_api_keys:
            return
        if bearer_token in config.session_api_keys:
            return
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    return check_openai_api_key


def _get_config(request: Request) -> Config:
    config = getattr(request.app.state, "config", None)
    if not isinstance(config, Config):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent server config is not available",
        )
    return config


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
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> OpenAIChatCompletionResponse:
    result = await run_chat_completion(
        request=body,
        config=_get_config(request),
        conversation_service=conversation_service,
        reusable_conversation_id=x_openhands_server_conversation_id,
    )
    response.headers["X-OpenHands-ServerConversation-ID"] = str(result.conversation_id)
    return result.response
