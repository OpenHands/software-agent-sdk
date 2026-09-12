from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse

from openhands.agent_server.config import Config
from openhands.agent_server.dependencies import check_session_api_key
from openhands.agent_server.docker_runtime.mediation import materialize_mcp_probe
from openhands.agent_server.docker_runtime.proxy import proxy_http
from openhands.agent_server.docker_runtime.routers import (
    _build_upstream_path,
    _workspace_or_404,
    get_registry,
)
from openhands.agent_server.init_router import require_initialized


class ConversationRuntimeRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        local_handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            config: Config = request.app.state.config
            if config.conversation_runtime == "docker":
                check_session_api_key(request, request.headers.get("x-session-api-key"))
                require_initialized(request)
                try:
                    conversation_id = UUID(
                        request.path_params["runtime_conversation_id"]
                    )
                except ValueError as exc:
                    raise HTTPException(422, "Invalid conversation id") from exc
                workspace = await _workspace_or_404(
                    get_registry(request), conversation_id
                )
                if request.url.path.endswith("/mcp/test"):
                    try:
                        payload = await materialize_mcp_probe(
                            await request.json(), config
                        )
                    except ValueError as exc:
                        raise HTTPException(
                            422, "Invalid runtime MCP configuration"
                        ) from exc
                    async with httpx.AsyncClient(timeout=130) as client:
                        response = await client.post(
                            workspace.host + request.url.path,
                            headers={"X-Session-API-Key": workspace.api_key or ""},
                            json=payload,
                        )
                    return JSONResponse(
                        {"detail": "Runtime MCP probe rejected"}
                        if response.is_error
                        else response.json(),
                        status_code=response.status_code,
                    )
                if not workspace.scoped_runtime_verified:
                    await _require_scoped_runtime_image(
                        workspace.host, workspace.api_key
                    )
                    workspace.scoped_runtime_verified = True
                return await proxy_http(
                    request,
                    workspace,
                    upstream_path=_build_upstream_path(request, request.url.path),
                )
            return await local_handler(request)

        return handle


async def _require_scoped_runtime_image(host: str, api_key: str | None) -> None:
    headers = {"X-Session-API-Key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{host}/server_info", headers=headers)
        response.raise_for_status()
        supported = "conversation_runtime_routes_v1" in response.json().get(
            "capabilities", []
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            502, "Could not check conversation runtime capabilities"
        ) from exc
    if not supported:
        raise HTTPException(
            409,
            "The configured conversation image does not support scoped runtime APIs. "
            "Build or select an agent-server image with conversation_runtime_routes_v1 "
            "and recreate the conversation container. "
            "Legacy runtime routes remain available.",
        )
