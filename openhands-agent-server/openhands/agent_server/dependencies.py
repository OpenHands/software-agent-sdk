from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import APIKeyHeader

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService


_SESSION_API_KEY_HEADER = APIKeyHeader(name="X-Session-API-Key", auto_error=False)


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


def create_websocket_session_api_key_dependency(config: Config):
    """Create a WebSocket session API key dependency with the given config.

    WebSocket connections cannot send custom headers directly from browsers,
    so we use query parameters instead.
    """

    def check_websocket_session_api_key(
        session_api_key: str | None = Query(None, alias="session_api_key"),
    ):
        """Check the session API key from query parameter for WebSocket connections."""
        if config.session_api_keys and session_api_key not in config.session_api_keys:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    return check_websocket_session_api_key


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
