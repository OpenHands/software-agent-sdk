"""Expose selected conversation REST operations through MCP."""

from contextvars import ContextVar

import httpx2
from fastapi import FastAPI
from fastmcp import FastMCP
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openhands.agent_server.config import Config


_session_api_key: ContextVar[str | None] = ContextVar(
    "mcp_session_api_key", default=None
)
_REST_TOOLS = (
    ("/api/conversations", "post", "start_conversation"),
    ("/api/conversations/{conversation_id}", "get", "get_conversation"),
    ("/api/conversations/{conversation_id}/events", "post", "send_message"),
)


class LocalSessionAuth:
    """Match the REST API's optional X-Session-API-Key requirement."""

    def __init__(self, app: ASGIApp, parent: FastAPI):
        self.app = app
        self.parent = parent

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        init = getattr(self.parent.state, "init_service", None)
        if init is not None and init.state != "ready":
            response = JSONResponse({"detail": "Server is initializing"}, 503)
            await response(scope, receive, send)
            return
        config: Config = self.parent.state.config
        key = Headers(scope=scope).get("x-session-api-key")
        if config.session_api_keys and key not in config.session_api_keys:
            response = JSONResponse({"detail": "Unauthorized"}, 401)
            await response(scope, receive, send)
            return
        token = _session_api_key.set(key)
        try:
            await self.app(scope, receive, send)
        finally:
            _session_api_key.reset(token)


def create_conversation_mcp(
    parent: FastAPI,
) -> tuple[FastMCP, ASGIApp, httpx2.AsyncClient]:
    """Convert only the chosen REST operations, retaining their schemas and behavior."""
    openapi = parent.openapi().copy()
    routes = openapi["paths"]
    openapi["paths"] = {
        path: {method: routes[path][method]} for path, method, _ in _REST_TOOLS
    }
    names = {
        routes[path][method]["operationId"]: name for path, method, name in _REST_TOOLS
    }

    async def forward_session_key(request: httpx2.Request) -> None:
        if key := _session_api_key.get():
            request.headers["X-Session-API-Key"] = key

    client = httpx2.AsyncClient(
        base_url="http://localhost",
        transport=httpx2.ASGITransport(app=parent),
        event_hooks={"request": [forward_session_key]},
    )
    mcp = FastMCP.from_openapi(
        openapi_spec=openapi,
        client=client,
        name="OpenHands",
        mcp_names=names,
        mask_error_details=True,
    )
    app = mcp.http_app(
        path="/",
        stateless_http=True,
        json_response=True,
        middleware=[Middleware(LocalSessionAuth, parent=parent)],
    )
    return mcp, app, client
