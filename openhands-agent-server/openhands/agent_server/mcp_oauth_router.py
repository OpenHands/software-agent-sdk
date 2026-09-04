"""MCP OAuth router for OpenHands agent-server.

Lets frontends drive the MCP OAuth 2.1 flow **before** a conversation
starts, so users can "Connect with OAuth" on the MCP settings page and
see immediate confirmation rather than getting a surprise browser popup
at conversation start.

Endpoints
---------
``POST /api/mcp/oauth/start``
    Initiate the OAuth flow for a remote MCP server. Returns an
    authorization URL that the frontend opens in a popup / new tab.
    The agent-server runs a local callback server to receive the
    redirect and exchange the code for a token.

``GET /api/mcp/oauth/status/{flow_id}``
    Poll the state of an in-progress OAuth flow. Returns ``pending``,
    ``completed`` (with server metadata), or ``failed``.

Token persistence
-----------------
Tokens (access + refresh) are stored on disk under the agent-server's
persistence directory so they survive process restarts. fastmcp's
``OAuth`` class automatically refreshes expired access tokens using the
stored refresh token; the user only re-authenticates if the refresh
token itself is revoked.

See https://github.com/OpenHands/software-agent-sdk/issues/3571
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.persistence.store import _get_persistence_dir
from openhands.sdk.logger import get_logger

logger = get_logger(__name__)

mcp_oauth_router = APIRouter(prefix="/mcp/oauth", tags=["MCP OAuth"])

# ---------------------------------------------------------------------------
# File-based token storage
# ---------------------------------------------------------------------------

_MCP_TOKEN_DIR_NAME = "mcp-oauth-tokens"


def _get_token_dir(request: Request) -> Path:
    """Resolve the on-disk directory for MCP OAuth tokens."""
    config = get_config(request)
    base = _get_persistence_dir(config)
    token_dir = base / _MCP_TOKEN_DIR_NAME
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir


def _make_file_token_storage(token_dir: Path):
    """Create a file-backed AsyncKeyValue store for MCP OAuth tokens.

    Uses the ``key_value`` library's ``FileTreeStore`` backend, which is
    a transitive dependency of fastmcp. Falls back to an in-memory store
    if the import fails for any reason.
    """
    try:
        from key_value.aio.stores.filetree.store import FileTreeStore

        return FileTreeStore(data_directory=token_dir)
    except Exception:
        logger.warning(
            "FileTreeStore not available; "
            "MCP OAuth tokens will be stored in memory only",
            exc_info=True,
        )
        from key_value.aio.stores.memory.store import MemoryStore

        return MemoryStore()


# ---------------------------------------------------------------------------
# In-flight OAuth flow registry
# ---------------------------------------------------------------------------


class _OAuthFlowState:
    """Tracks one in-progress OAuth flow."""

    __slots__ = (
        "flow_id",
        "server_url",
        "server_name",
        "status",
        "authorization_url",
        "callback_port",
        "error",
        "created_at",
        # asyncio.Event used by the background task to signal the auth URL
        # is ready (set from the OAuth redirect_handler running inside the
        # background event loop).
        "_auth_url_event",
        "_background_task",
    )

    def __init__(self, server_url: str, server_name: str) -> None:
        self.flow_id = str(uuid.uuid4())
        self.server_url = server_url
        self.server_name = server_name
        self.status: Literal["pending", "completed", "failed"] = "pending"
        self.authorization_url: str | None = None
        self.callback_port: int | None = None
        self.error: str | None = None
        self.created_at = datetime.now(timezone.utc)
        # Coordination: set by the redirect_handler, awaited by the
        # background-loop bootstrap so it can surface the URL early.
        self._auth_url_event: asyncio.Event | None = None
        self._background_task: asyncio.Task | None = None


# flow_id -> flow state; cleaned up on terminal status read.
_active_flows: dict[str, _OAuthFlowState] = {}
_flows_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class MCPOAuthStartRequest(BaseModel):
    """Body for ``POST /api/mcp/oauth/start``."""

    server_url: str = Field(
        ...,
        min_length=1,
        description="Full URL of the remote MCP server endpoint.",
    )
    server_name: str = Field(
        default="mcp-server",
        min_length=1,
        max_length=128,
        description="Human-readable name for this server.",
    )
    scopes: list[str] | None = Field(
        default=None,
        description="OAuth scopes to request (optional override).",
    )
    client_id: str | None = Field(
        default=None,
        description=(
            "Pre-registered OAuth client ID. When provided, "
            "skips Dynamic Client Registration."
        ),
    )
    client_secret: str | None = Field(
        default=None,
        description="OAuth client secret (optional, used with client_id).",
    )
    timeout: float = Field(
        default=300.0,
        gt=0,
        le=600,
        description="Seconds to wait for the user to complete the OAuth flow.",
    )


class MCPOAuthStartResponse(BaseModel):
    """Response from ``POST /api/mcp/oauth/start``."""

    flow_id: str = Field(description="Unique identifier for this OAuth flow.")
    authorization_url: str = Field(
        description="URL the frontend should open for user authorization."
    )
    callback_port: int = Field(
        description="Port where the local callback server is listening."
    )
    expires_in: float = Field(description="Seconds before the flow times out.")


class MCPOAuthStatusResponse(BaseModel):
    """Response from ``GET /api/mcp/oauth/status/{flow_id}``."""

    status: Literal["pending", "completed", "failed"]
    server_url: str | None = None
    server_name: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# OAuth flow driver (async, runs as a background task)
# ---------------------------------------------------------------------------


async def _drive_oauth_flow(
    flow: _OAuthFlowState,
    token_dir: Path,
    scopes: list[str] | None,
    client_id: str | None,
    client_secret: str | None,
    timeout: float,
) -> None:
    """Drive the full MCP OAuth 2.1 handshake.

    Uses fastmcp's ``OAuth`` class with a custom ``redirect_handler`` that
    captures the authorization URL (so we can return it to the frontend)
    instead of opening a browser.

    The flow:
    1. Make an HTTP request to the MCP server → receive 401
    2. fastmcp's auth handler discovers the authorization server
    3. Dynamic Client Registration (unless ``client_id`` is given)
    4. ``redirect_handler`` fires → we capture the auth URL and set the
       event so ``POST /start`` can return it immediately
    5. ``callback_handler`` starts a local HTTP server waiting for the
       redirect callback from the OAuth provider
    6. User authorizes in the browser
    7. Callback server receives the auth code
    8. Token exchange → tokens stored on disk via file-backed storage
    """
    from fastmcp.client.auth import OAuth

    token_storage = _make_file_token_storage(token_dir)

    assert flow._auth_url_event is not None  # set by the caller

    class _FrontendOAuth(OAuth):
        """Subclass that captures the auth URL instead of opening a browser."""

        async def redirect_handler(self, authorization_url: str) -> None:
            flow.authorization_url = authorization_url
            if hasattr(self, "redirect_port"):
                flow.callback_port = self.redirect_port
            assert flow._auth_url_event is not None
            flow._auth_url_event.set()
            logger.info("MCP OAuth authorization URL ready: %s", authorization_url)

    oauth = _FrontendOAuth(
        mcp_url=flow.server_url,
        scopes=scopes,
        client_name="OpenHands Agent Canvas",
        token_storage=token_storage,
        client_id=client_id,
        client_secret=client_secret,
        callback_timeout=timeout,
    )

    try:
        # Make a request to the MCP server through httpx with the OAuth
        # auth provider. The MCP server returns 401, which triggers the
        # full OAuth flow (discovery → DCR → redirect → callback → token
        # exchange). The call blocks until the user completes auth.
        async with httpx.AsyncClient(auth=oauth, follow_redirects=True) as client:
            await client.get(flow.server_url)

        flow.status = "completed"
        logger.info("MCP OAuth flow completed for server %r", flow.server_name)
    except TimeoutError:
        flow.status = "failed"
        flow.error = f"OAuth flow timed out after {timeout} seconds"
        logger.info("MCP OAuth flow timed out for server %r", flow.server_name)
    except Exception as exc:
        flow.status = "failed"
        flow.error = (
            f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        )
        logger.warning(
            "MCP OAuth flow failed for server %r: %s",
            flow.server_name,
            flow.error,
            exc_info=True,
        )
        # If the flow failed before the auth URL was captured, unblock
        # the waiter so POST /start returns the error instead of timing out.
        if not flow._auth_url_event.is_set():
            flow._auth_url_event.set()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_AUTH_URL_WAIT_SECONDS = 10.0  # max time to wait for discovery + DCR


@mcp_oauth_router.post(
    "/start",
    response_model=MCPOAuthStartResponse,
    summary="Start an MCP OAuth flow",
    description=(
        "Initiate an OAuth 2.1 flow for a remote MCP server. "
        "Returns an authorization URL that the frontend should open in a "
        "popup or new tab. The agent-server runs a local callback server "
        "to receive the OAuth redirect, exchanges the code for tokens, "
        "and persists them to disk.\n\n"
        "Poll ``GET /api/mcp/oauth/status/{flow_id}`` to check completion."
    ),
)
async def start_mcp_oauth(
    request: MCPOAuthStartRequest, http_request: Request
) -> MCPOAuthStartResponse:
    """Start the MCP OAuth flow and return the authorization URL."""
    token_dir = _get_token_dir(http_request)

    flow = _OAuthFlowState(
        server_url=request.server_url,
        server_name=request.server_name,
    )
    flow._auth_url_event = asyncio.Event()

    with _flows_lock:
        _active_flows[flow.flow_id] = flow

    # Launch the OAuth handshake as a background task on the current
    # event loop. The task will set ``flow._auth_url_event`` once the
    # authorization URL is known (usually <5 s for discovery + DCR).
    flow._background_task = asyncio.create_task(
        _drive_oauth_flow(
            flow,
            token_dir,
            request.scopes,
            request.client_id,
            request.client_secret,
            request.timeout,
        ),
        name=f"mcp-oauth-{flow.flow_id[:8]}",
    )

    # Wait for the authorization URL (or early failure).
    try:
        await asyncio.wait_for(
            flow._auth_url_event.wait(),
            timeout=_AUTH_URL_WAIT_SECONDS,
        )
    except asyncio.TimeoutError:
        # Discovery or DCR took too long.
        with _flows_lock:
            _active_flows.pop(flow.flow_id, None)
        flow._background_task.cancel()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"Could not obtain authorization URL within "
                f"{_AUTH_URL_WAIT_SECONDS:.0f} seconds. "
                "The MCP server may be unreachable or does not support OAuth."
            ),
        )

    # The event was set — check whether it was because of success or failure.
    if flow.status == "failed":
        with _flows_lock:
            _active_flows.pop(flow.flow_id, None)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=flow.error or "OAuth flow failed during initialization",
        )

    assert flow.authorization_url is not None

    return MCPOAuthStartResponse(
        flow_id=flow.flow_id,
        authorization_url=flow.authorization_url,
        callback_port=flow.callback_port or 0,
        expires_in=request.timeout,
    )


@mcp_oauth_router.get(
    "/status/{flow_id}",
    response_model=MCPOAuthStatusResponse,
    summary="Check MCP OAuth flow status",
    description=(
        "Poll the status of an in-progress MCP OAuth flow. "
        "Returns ``pending`` while waiting for the user to authorize, "
        "``completed`` when tokens have been persisted, or ``failed`` "
        "with an error message."
    ),
)
async def get_mcp_oauth_status(flow_id: str) -> MCPOAuthStatusResponse:
    """Check the status of an OAuth flow."""
    with _flows_lock:
        flow = _active_flows.get(flow_id)

    if flow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth flow {flow_id!r} not found or already expired",
        )

    response = MCPOAuthStatusResponse(
        status=flow.status,
        server_url=flow.server_url if flow.status == "completed" else None,
        server_name=flow.server_name if flow.status == "completed" else None,
        error=flow.error if flow.status == "failed" else None,
    )

    # Clean up terminal flows so they don't leak memory.
    if flow.status in ("completed", "failed"):
        with _flows_lock:
            _active_flows.pop(flow_id, None)

    return response
