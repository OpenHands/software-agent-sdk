import asyncio
import os
import sys
import threading
import time
from importlib.metadata import version
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from openhands.sdk.tool.registry import list_usable_tools
from openhands.tools.terminal.timeout_policy import (
    get_max_foreground_timeout_seconds,
    get_runtime_idle_timeout_seconds,
)


server_details_router = APIRouter(prefix="", tags=["Server Details"])
_start_time = time.time()
_last_event_time = time.time()
_initialization_complete = asyncio.Event()
_execution_guard = threading.Lock()
_active_executions = 0
_idle_pause_fenced = False


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
            "credential_binding_v1",
            "credential_binding_readiness_probe_v1",
            "credential_binding_activation_guard_v1",
        ]
    )
    max_foreground_terminal_timeout_seconds: float | None = Field(
        default_factory=lambda: get_max_foreground_timeout_seconds()
    )

    docs: str = "/docs"
    redoc: str = "/redoc"


class IdlePauseFenceRequest(BaseModel):
    minimum_idle_seconds: int = Field(ge=1)


class IdlePauseFenceResult(BaseModel):
    claimed: bool
    idle_time: float
    active_executions: int


def update_last_execution_time():
    global _last_event_time
    with _execution_guard:
        _last_event_time = time.time()


def begin_execution() -> bool:
    global _active_executions, _last_event_time
    with _execution_guard:
        if _idle_pause_fenced:
            return False
        _active_executions += 1
        _last_event_time = time.time()
        return True


def finish_execution() -> None:
    global _active_executions, _last_event_time
    with _execution_guard:
        if _active_executions <= 0:
            raise RuntimeError("execution guard underflow")
        _active_executions -= 1
        _last_event_time = time.time()


def claim_idle_pause(minimum_idle_seconds: int) -> IdlePauseFenceResult:
    global _idle_pause_fenced
    with _execution_guard:
        idle_time = time.time() - _last_event_time
        if _idle_pause_fenced or _active_executions or idle_time < minimum_idle_seconds:
            return IdlePauseFenceResult(
                claimed=False,
                idle_time=idle_time,
                active_executions=_active_executions,
            )
        _idle_pause_fenced = True
        return IdlePauseFenceResult(
            claimed=True,
            idle_time=idle_time,
            active_executions=0,
        )


def release_idle_pause() -> None:
    global _idle_pause_fenced
    with _execution_guard:
        _idle_pause_fenced = False


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


def build_server_info(
    conversation_runtime: Literal["local", "docker"] = "local",
) -> ServerInfo:
    now = time.time()
    return ServerInfo(
        uptime=int(now - _start_time),
        idle_time=int(now - _last_event_time),
        conversation_runtime=conversation_runtime,
    )


@server_details_router.get("/server_info")
async def get_server_info(request: Request) -> ServerInfo:
    return build_server_info(request.app.state.config.conversation_runtime)


def _require_session_key(request: Request, session_api_key: str | None) -> None:
    configured_keys = request.app.state.config.session_api_keys
    if not configured_keys or session_api_key not in configured_keys:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)


@server_details_router.post("/idle_pause_fence")
async def acquire_idle_pause_fence(
    body: IdlePauseFenceRequest,
    request: Request,
    session_api_key: str | None = Header(default=None, alias="X-Session-API-Key"),
) -> IdlePauseFenceResult:
    """Atomically fence new execution only when the runtime is genuinely idle."""
    _require_session_key(request, session_api_key)
    return claim_idle_pause(body.minimum_idle_seconds)


@server_details_router.post("/idle_pause_fence/release")
async def release_idle_pause_fence(
    request: Request,
    session_api_key: str | None = Header(default=None, alias="X-Session-API-Key"),
) -> dict[str, str]:
    """Release a claimed fence when runtime-api could not commit the pause."""
    _require_session_key(request, session_api_key)
    release_idle_pause()
    return {"status": "released"}
