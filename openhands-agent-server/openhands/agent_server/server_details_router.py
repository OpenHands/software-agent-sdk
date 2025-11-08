import time
from importlib.metadata import version

from fastapi import APIRouter
from pydantic import BaseModel


server_details_router = APIRouter(prefix="", tags=["Server Details"])
_start_time = time.time()
_last_event_time = time.time()


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


@server_details_router.get("/alive")
async def alive():
    return {"status": "ok"}


@server_details_router.get("/health")
async def health() -> str:
    return "OK"


@server_details_router.get("/server_info")
async def get_server_info() -> ServerInfo:
    now = time.time()
    return ServerInfo(
        uptime=int(now - _start_time),
        idle_time=int(now - _last_event_time),
    )
