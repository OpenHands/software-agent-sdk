import asyncio
import os
import sys
import time
from importlib.metadata import version
from typing import Final, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from openhands.agent_server.launch import configured_browser_available
from openhands.sdk.tool import BROWSER_TOOL_NAME
from openhands.sdk.tool.registry import list_registered_tools, list_usable_tools
from openhands.tools.terminal.timeout_policy import (
    get_max_foreground_timeout_seconds,
    get_runtime_idle_timeout_seconds,
)


server_details_router = APIRouter(prefix="", tags=["Server Details"])
_start_time = time.time()
_last_event_time = time.time()
_initialization_complete = asyncio.Event()


def _package_version(dist_name: str) -> str:
    try:
        return version(dist_name)
    except Exception:
        return "unknown"


class HealthStatus(BaseModel):
    status: str


class ServerInfo(BaseModel):
    uptime: float
    idle_time: float
    title: str = "OpenHands Agent Server"

    version: str = Field(
        default_factory=lambda: _package_version("openhands-agent-server")
    )
    sdk_version: str = Field(default_factory=lambda: _package_version("openhands-sdk"))
    tools_version: str = Field(
        default_factory=lambda: _package_version("openhands-tools")
    )
    workspace_version: str = Field(
        default_factory=lambda: _package_version("openhands-workspace")
    )

    build_git_sha: str = Field(
        default_factory=lambda: os.environ.get("OPENHANDS_BUILD_GIT_SHA", "unknown")
    )
    build_git_ref: str = Field(
        default_factory=lambda: os.environ.get("OPENHANDS_BUILD_GIT_REF", "unknown")
    )
    python_version: str = Field(default_factory=lambda: sys.version)
    usable_tools: list[str] = Field(default_factory=lambda: list_usable_tools())
    runtime_idle_timeout_seconds: float | None = Field(
        default_factory=lambda: get_runtime_idle_timeout_seconds()
    )
    conversation_runtime: Literal["local", "docker"] = "local"
    capabilities: list[str] = Field(
        default_factory=lambda: [
            "conversation_runtime_routes_v1",
            "profile_secret_scope_v1",
            "profile_persona_v1",
            "credential_binding_v1",
            "credential_binding_readiness_probe_v1",
            "credential_binding_activation_guard_v1",
            # Concurrent creates for one conversation id are deduplicated under
            # the conversation's lifecycle lock, so a client may safely re-send
            # a create whose response it never saw.
            "idempotent_conversation_create_v1",
            "tool_catalog_v1",
            "agent_profile_draft_materialize_v1",
            "agent_launch_pipeline_v1",
            "profile_public_skills_v1",
        ]
    )
    app_backend_ingress_url: str | None = Field(
        default=None,
        description=(
            "Separate browser origin for authenticated Canvas App backend sessions"
        ),
    )
    max_foreground_terminal_timeout_seconds: float | None = Field(
        default_factory=lambda: get_max_foreground_timeout_seconds()
    )

    docs: str = "/docs"
    redoc: str = "/redoc"


def update_last_execution_time():
    global _last_event_time
    _last_event_time = time.time()


# --- WebSocket keep-alive for the runtime idle timer ------------------------
#
# A connected WebSocket is user presence, but it never reaches the file/bash/git
# routers and does not necessarily persist an event, so it never refreshes
# ``_last_event_time``.  A user who merely *watches* a conversation therefore
# accrues idle time 1:1 until the runtime reaps the pod
# (OH_RUNTIME_IDLE_TIMEOUT_SECONDS, default 20 min) and every socket on the pod
# drops at once.
#
# We refresh the timer for as long as at least one socket is attached, plus
# immediately on connect and on each inbound frame.  Dead sockets are already
# reaped by the server's protocol-level ping/pong timeout, so "attached" means
# "the peer is still answering at the transport level"; we deliberately do not
# invent a second liveness deadline here.
WS_KEEPALIVE_INTERVAL_SECONDS: Final[float] = 15.0
"""How often the background loop refreshes the idle timer for attached sockets."""

# Live WebSocket connections, counted per connection so several clients (or
# several conversations) on one pod are tracked independently.
_active_ws_connections: int = 0


def register_websocket_connection() -> None:
    """Record a newly authenticated WebSocket and refresh the idle timer."""
    global _active_ws_connections
    _active_ws_connections += 1
    update_last_execution_time()


def unregister_websocket_connection() -> None:
    """Record a WebSocket close."""
    global _active_ws_connections
    _active_ws_connections = max(0, _active_ws_connections - 1)


def signal_websocket_activity() -> None:
    """Refresh the idle timer on behalf of a live WebSocket client."""
    if _active_ws_connections > 0:
        update_last_execution_time()


def refresh_websocket_keepalive() -> None:
    """Periodic tick: hold the runtime open while any socket is attached."""
    if _active_ws_connections > 0:
        update_last_execution_time()


def websocket_keepalive_interval_seconds() -> float:
    """Keep-alive tick period."""
    return WS_KEEPALIVE_INTERVAL_SECONDS


def mark_initialization_complete() -> None:
    """Mark the server as fully initialized and ready to serve requests.

    This should be called after all services (VSCode, tool preload, etc.)
    have finished initializing. Until this is called, the /ready endpoint will
    return 503 Service Unavailable.
    """
    _initialization_complete.set()


@server_details_router.get("/alive")
async def alive() -> HealthStatus:
    """Basic liveness check - returns OK if the server process is running."""
    return HealthStatus(status="ok")


@server_details_router.get("/health")
async def health() -> HealthStatus:
    """Basic health check - returns OK if the server process is running."""
    return HealthStatus(status="ok")


@server_details_router.get("/ready")
async def ready(response: Response) -> dict[str, str]:
    """Readiness check - returns OK only if the server has completed initialization.

    This endpoint should be used by Kubernetes readiness probes to determine
    when the pod is ready to receive traffic. Returns 503 during initialization.
    """
    if _initialization_complete.is_set():
        return {"status": "ready"}
    else:
        response.status_code = 503
        return {"status": "initializing", "message": "Server is still initializing"}


def _usable_tools(browser_available: bool | None) -> list[str]:
    """Usable tools, with the browser fixed by config as the tool catalog does."""
    tools = list_usable_tools()
    if browser_available is None:
        return tools
    if not browser_available:
        return [name for name in tools if name != BROWSER_TOOL_NAME]
    if BROWSER_TOOL_NAME not in tools and BROWSER_TOOL_NAME in list_registered_tools():
        tools.append(BROWSER_TOOL_NAME)
    return tools


def build_server_info(
    conversation_runtime: Literal["local", "docker"] = "local",
    app_backend_ingress_url: str | None = None,
    browser_available: bool | None = None,
) -> ServerInfo:
    """Build the server info; ``browser_available`` None probes this process."""
    now = time.time()
    info = ServerInfo(
        uptime=int(now - _start_time),
        idle_time=int(now - _last_event_time),
        conversation_runtime=conversation_runtime,
        app_backend_ingress_url=app_backend_ingress_url,
        usable_tools=_usable_tools(browser_available),
    )
    if app_backend_ingress_url:
        info.capabilities.append("canvas_app_backend_bridge_v1")
    return info


@server_details_router.get("/server_info")
async def get_server_info(request: Request) -> ServerInfo:
    config = request.app.state.config
    return build_server_info(
        config.conversation_runtime,
        app_backend_ingress_url=config.app_backend_public_url,
        browser_available=configured_browser_available(config),
    )
