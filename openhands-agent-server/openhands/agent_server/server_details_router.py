import asyncio
import os
import sys
import time
from importlib.metadata import version

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from openhands.agent_server.ssh_service import get_ssh_status


server_details_router = APIRouter(prefix="", tags=["Server Details"])
_start_time = time.time()
_last_event_time = time.time()
_initialization_complete = asyncio.Event()


def _package_version(dist_name: str) -> str:
    try:
        return version(dist_name)
    except Exception:
        return "unknown"


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

    docs: str = "/docs"
    redoc: str = "/redoc"


def update_last_execution_time():
    global _last_event_time
    _last_event_time = time.time()


def mark_initialization_complete() -> None:
    """Mark the server as fully initialized and ready to serve requests.

    This should be called after all services (VSCode, desktop, tool preload, etc.)
    have finished initializing. Until this is called, the /ready endpoint will
    return 503 Service Unavailable.
    """
    _initialization_complete.set()


@server_details_router.get("/alive")
async def alive():
    """Basic liveness check - returns OK if the server process is running."""
    return {"status": "ok"}


@server_details_router.get("/health")
async def health() -> str:
    """Basic health check - returns OK if the server process is running."""
    return "OK"


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


@server_details_router.get("/server_info")
async def get_server_info() -> ServerInfo:
    now = time.time()
    return ServerInfo(
        uptime=int(now - _start_time),
        idle_time=int(now - _last_event_time),
    )


class SSHStatus(BaseModel):
    """SSH service status information."""

    enabled: bool = Field(description="Whether SSH is enabled in server configuration")
    running: bool = Field(description="Whether SSH server is currently running")
    error: str | None = Field(
        default=None,
        description="Error message if SSH failed to start, or None if successful",
    )
    port: int = Field(description="The SSH port number")


@server_details_router.get("/ssh_status")
async def get_ssh_status_endpoint() -> SSHStatus:
    """Get the current SSH service status.

    Returns status information about the SSH service, including whether it's
    running and any error messages. This can be used by the frontend to show
    appropriate error messages when users try to connect via SSH.
    """
    status = get_ssh_status()
    return SSHStatus(**status)
