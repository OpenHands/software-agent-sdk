from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request, WebSocket, status
from fastapi.security import APIKeyHeader

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config, get_default_config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.desktop_service import DesktopService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.vscode_service import VSCodeService


_SESSION_API_KEY_HEADER = APIKeyHeader(name="X-Session-API-Key", auto_error=False)


# Back-compat shim for tests that patch this symbol at this import location
get_default_config = get_default_config


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_conversation_service(request: Request) -> ConversationService:
    # Graceful fallback so tests that construct app without full startup don't fail
    svc = getattr(request.app.state, "conversation_service", None)
    if svc is None:
        cfg = getattr(request.app.state, "config", get_default_config())
        svc = ConversationService.get_instance(cfg)
        request.app.state.conversation_service = svc
    return svc


def get_bash_event_service(request: Request) -> BashEventService:
    svc = getattr(request.app.state, "bash_event_service", None)
    if svc is None:
        cfg = getattr(request.app.state, "config", get_default_config())
        svc = BashEventService(bash_events_dir=cfg.bash_events_dir)
        request.app.state.bash_event_service = svc
    return svc


# WebSocket-aware DI helpers (WebSocket has no Request object)


def get_conversation_service_ws(websocket: WebSocket) -> ConversationService:
    svc = getattr(websocket.app.state, "conversation_service", None)
    if svc is None:
        cfg = getattr(websocket.app.state, "config", get_default_config())
        svc = ConversationService.get_instance(cfg)
        websocket.app.state.conversation_service = svc
    return svc


def get_bash_event_service_ws(websocket: WebSocket) -> BashEventService:
    svc = getattr(websocket.app.state, "bash_event_service", None)
    if svc is None:
        cfg = getattr(websocket.app.state, "config", get_default_config())
        svc = BashEventService(bash_events_dir=cfg.bash_events_dir)
        websocket.app.state.bash_event_service = svc
    return svc


def websocket_session_api_key_dependency_ws(
    websocket: WebSocket,
    session_api_key: str | None = Query(None, alias="session_api_key"),
) -> None:
    cfg = getattr(websocket.app.state, "config", get_default_config())
    if cfg.session_api_keys and session_api_key not in cfg.session_api_keys:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)


def get_vscode_service(request: Request) -> VSCodeService | None:
    # VSCode may be disabled by config
    return getattr(request.app.state, "vscode_service", None)


def get_desktop_service(request: Request) -> DesktopService | None:
    return getattr(request.app.state, "desktop_service", None)


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


def session_api_key_dependency(
    config: Config = Depends(get_config),
    session_api_key: str | None = Depends(_SESSION_API_KEY_HEADER),
) -> None:
    """Session API key dependency that reads Config from app.state.

    Always safe to include; if no API keys configured, it is a no-op.
    """
    if config.session_api_keys and session_api_key not in config.session_api_keys:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)


def websocket_session_api_key_dependency(
    config: Config = Depends(get_config),
    session_api_key: str | None = Query(None, alias="session_api_key"),
) -> None:
    """WebSocket auth dependency that reads Config from app.state and validates
    the session_api_key provided as a query parameter.
    """
    if config.session_api_keys and session_api_key not in config.session_api_keys:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)


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
