from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie, APIKeyHeader

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService


# Cookie name used to authenticate the workspace static-file routes.
# Intentionally distinct from the header name: the cookie is ONLY honored
# by the workspace router (so iframes / <img> can load workspace files),
# and is rejected by every other API endpoint.
WORKSPACE_SESSION_COOKIE_NAME = "oh_workspace_session_key"

_SESSION_API_KEY_HEADER = APIKeyHeader(name="X-Session-API-Key", auto_error=False)
_WORKSPACE_SESSION_COOKIE = APIKeyCookie(
    name=WORKSPACE_SESSION_COOKIE_NAME, auto_error=False
)


def create_session_api_key_dependency(config: Config):
    """Create a session API key dependency with the given config."""

    def check_session_api_key(
        session_api_key: str | None = Depends(_SESSION_API_KEY_HEADER),
    ):
        """Check the session API key and throw an exception if incorrect. Having this as
        a dependency means it appears in OpenAPI Docs
        """
        if config.session_api_keys and session_api_key not in config.session_api_keys:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    return check_session_api_key


def create_workspace_session_dependency(config: Config):
    """Auth dependency for the workspace static-file routes.

    Accepts EITHER the standard ``X-Session-API-Key`` header OR the
    ``oh_workspace_session_key`` cookie (minted by
    ``POST /api/auth/workspace-session``).
    The cookie is required because browsers cannot attach custom headers to
    ``<iframe src>`` or ``<img src>`` requests, which is how the canvas
    frontend embeds workspace artifacts. The cookie is deliberately scoped
    to this router only; no other endpoint honors it.
    """

    def check_workspace_session(
        header_key: str | None = Depends(_SESSION_API_KEY_HEADER),
        cookie_key: str | None = Depends(_WORKSPACE_SESSION_COOKIE),
    ):
        if not config.session_api_keys:
            return
        for candidate in (header_key, cookie_key):
            if candidate and candidate in config.session_api_keys:
                return
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    return check_workspace_session


def get_conversation_service(request: Request):
    """Get the conversation service from app state.

    This dependency ensures that the conversation service is properly initialized
    through the application lifespan context manager.
    """

    service = getattr(request.app.state, "conversation_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation service is not available",
        )
    return service


async def get_event_service(
    conversation_id: UUID,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> EventService:
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )
    return event_service
