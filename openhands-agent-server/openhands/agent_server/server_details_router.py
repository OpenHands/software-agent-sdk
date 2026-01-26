import time
from importlib.metadata import version

from fastapi import APIRouter, Response
from pydantic import BaseModel


server_details_router = APIRouter(prefix="", tags=["Server Details"])
_start_time = time.time()
_last_event_time = time.time()
_initialization_complete = False


class ServerInfo(BaseModel):
    uptime: float
    idle_time: float
    title: str = "OpenHands Agent Server"
    version: str = version("openhands-agent-server")
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
    global _initialization_complete
    _initialization_complete = True


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
    if _initialization_complete:
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
