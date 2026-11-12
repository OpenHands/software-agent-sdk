"""What a route can ask the server for.

A route names what it needs in its signature, ``Depends(get_settings_store)``
for example, and FastAPI hands it over. Every provider here resolves from
``get_server_context``, which FastAPI calls once per request or WebSocket, so
all the dependencies of one request come from the same
:class:`~openhands.agent_server.server_context.ServerContext`, including the
router-level ones such as ``check_session_api_key``. A test replaces any of
them through ``app.dependency_overrides``.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, WebSocketException, status
from fastapi.exceptions import RequestValidationError
from fastapi.security import APIKeyCookie, APIKeyHeader
from pydantic import TypeAdapter, ValidationError
from starlette.requests import HTTPConnection

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_registry import ConversationRegistry
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.persistence import (
    FileSecretsStore,
    FileSettingsStore,
    FileWorkspacesStore,
    ProviderConnectionStore,
    ServerStores,
)
from openhands.agent_server.server_context import ServerContext, get_server_context
from openhands.agent_server.vscode_service import VSCodeService
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.profiles.agent_profile_store import AgentProfileStore
from openhands.sdk.utils.cipher import Cipher


# Cookie name used to authenticate the workspace static-file routes.
# Intentionally distinct from the header name: the cookie is ONLY honored
# by the workspace router (so iframes / <img> can load workspace files),
# and is rejected by every other API endpoint.
WORKSPACE_SESSION_COOKIE_NAME = "oh_workspace_session_key"

_SESSION_API_KEY_HEADER = APIKeyHeader(name="X-Session-API-Key", auto_error=False)
_WORKSPACE_SESSION_COOKIE = APIKeyCookie(
    name=WORKSPACE_SESSION_COOKIE_NAME, auto_error=False
)


# Declared with ``Annotated`` so that they are required arguments: calling a
# provider by hand with only a request does not type-check, instead of leaving
# a ``Depends`` object where the context should be.
_Context = Annotated[ServerContext, Depends(get_server_context)]


def get_config(context: _Context) -> Config:
    """The config this server currently runs with.

    Read from the server context at request time, so the config delivered via
    ``POST /api/init`` takes effect immediately without restarting the server
    or re-registering routes.
    """
    return context.config


def get_cipher(config: Annotated[Config, Depends(get_config)]) -> Cipher | None:
    """The configured cipher (``None`` when ``OH_SECRET_KEY`` is unset)."""
    return config.cipher


def get_stores(context: _Context) -> ServerStores:
    """The persistent stores built for this server's current config.

    For code that works across several stores; a route that uses one store
    asks for that store.
    """
    return context.stores


_Stores = Annotated[ServerStores, Depends(get_stores)]


def get_settings_store(stores: _Stores) -> FileSettingsStore:
    return stores.settings


def get_secrets_store(stores: _Stores) -> FileSecretsStore:
    return stores.secrets


def get_provider_connections_store(stores: _Stores) -> ProviderConnectionStore:
    return stores.provider_connections


def get_workspaces_store(stores: _Stores) -> FileWorkspacesStore:
    return stores.workspaces


def get_llm_profile_store(stores: _Stores) -> LLMProfileStore:
    return stores.llm_profiles


def get_agent_profile_store(stores: _Stores) -> AgentProfileStore:
    return stores.agent_profiles


def get_conversation_registry(context: _Context) -> ConversationRegistry:
    return context.conversation_registry


def get_vscode_service(context: _Context) -> VSCodeService | None:
    """This server's VSCode service (``None`` when VSCode is disabled)."""
    return context.vscode_service


def _unavailable(connection: HTTPConnection, detail: str) -> Exception:
    """A started-service lookup that found none: 503, or 1013 on a WebSocket."""
    if connection.scope["type"] == "websocket":
        return WebSocketException(code=status.WS_1013_TRY_AGAIN_LATER, reason=detail)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail)


def check_session_api_key(
    config: Annotated[Config, Depends(get_config)],
    session_api_key: Annotated[str | None, Depends(_SESSION_API_KEY_HEADER)],
) -> None:
    """Reject the request if the supplied key is not in the current session keys."""
    if config.session_api_keys and session_api_key not in config.session_api_keys:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)


def check_workspace_session(
    config: Annotated[Config, Depends(get_config)],
    header_key: Annotated[str | None, Depends(_SESSION_API_KEY_HEADER)],
    cookie_key: Annotated[str | None, Depends(_WORKSPACE_SESSION_COOKIE)],
) -> None:
    """Auth dependency for the workspace static-file routes.

    Accepts EITHER the standard ``X-Session-API-Key`` header OR the
    ``oh_workspace_session_key`` cookie (minted by
    ``POST /api/auth/workspace-session``).
    The cookie is required because browsers cannot attach custom headers to
    ``<iframe src>`` or ``<img src>`` requests, which is how the canvas
    frontend embeds workspace artifacts. The cookie is deliberately scoped
    to this router only; no other endpoint honors it.
    """
    if not config.session_api_keys:
        return
    for candidate in (header_key, cookie_key):
        if candidate and candidate in config.session_api_keys:
            return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED)


def get_conversation_service(
    connection: HTTPConnection, context: _Context
) -> ConversationService:
    service = context.conversation_service
    if service is None:
        raise _unavailable(connection, "Conversation service is not available")
    return service


def get_server_bash_event_service(
    connection: HTTPConnection, context: _Context
) -> BashEventService:
    """The server-wide bash service (not a conversation runtime's own)."""
    service = context.bash_event_service
    if service is None:
        raise _unavailable(connection, "Bash event service is not available")
    return service


async def get_bash_event_service(
    request: Request, context: _Context
) -> BashEventService:
    if "runtime_conversation_id" in request.path_params:
        event_service: EventService | None = getattr(
            request.state, "runtime_event_service", None
        )
        if event_service is None:
            # FastAPI still resolves this dependency when the router dependency
            # bind_local_conversation_runtime failed validation, so report that
            # validation error instead of reading state it never set.
            raw_id = request.path_params["runtime_conversation_id"]
            try:
                TypeAdapter(UUID).validate_python(raw_id)
            except ValidationError as exc:
                raise RequestValidationError(
                    [
                        {**error, "loc": ("path", "runtime_conversation_id")}
                        for error in exc.errors(include_url=False)
                    ]
                ) from exc
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Conversation runtime is not bound",
            )
        if event_service.bash_event_service is None:
            event_service.bash_event_service = BashEventService(
                bash_events_dir=event_service.conversation_dir / "bash_events",
                default_cwd=event_service.get_conversation().workspace.working_dir,
                cipher=event_service.cipher,
            )
        return event_service.bash_event_service
    # Looked up only here: a runtime's own bash service does not need the
    # server-wide one to be running.
    return get_server_bash_event_service(request, context)


async def get_event_service(
    conversation_id: UUID,
    conversation_service: Annotated[
        ConversationService, Depends(get_conversation_service)
    ],
    registry: Annotated[ConversationRegistry, Depends(get_conversation_registry)],
) -> EventService:
    if registry.serves_persisted_event_reads(conversation_id):
        event_service = await conversation_service.get_persisted_event_service(
            conversation_id
        )
    else:
        event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )
    return event_service
