import logging
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


@server_details_router.get("/test-logging")
async def test_logging(
    logger_name: str | None = None, level: int = 50, msg: str = "Test Logging!"
) -> str:
    logger = logging.getLogger(logger_name or None)
    logger.log(level, msg)
    return msg


class HandlerInfo(BaseModel):
    name: str | None
    level: int | str | None


class LoggerInfo(BaseModel):
    name: str
    level: int | str | None
    propagate: bool
    handlers: list[HandlerInfo]


@server_details_router.get("/get-logger-info")
async def get_logger_names() -> list[LoggerInfo]:
    results = []
    for logger_name in logging.root.manager.loggerDict:
        logger = logging.getLogger(logger_name)
        results.append(
            LoggerInfo(
                name=logger.name,
                level=logger.level,
                propagate=logger.propagate,
                handlers=[
                    HandlerInfo(
                        name=handler.name,
                        level=handler.level,
                    )
                    for handler in logger.handlers
                ],
            )
        )
    return results


@server_details_router.get("/set-log-level")
async def set_log_level(
    logger_name: str | None = None, handler_name: str | None = None, level: int = 50
) -> bool:
    logger = logging.getLogger(logger_name or None)
    if not handler_name:
        logger.setLevel(level)
        return True

    handler = next(
        handler for handler in logger.handlers if handler.name == handler_name
    )
    handler.setLevel(level)
    return True
