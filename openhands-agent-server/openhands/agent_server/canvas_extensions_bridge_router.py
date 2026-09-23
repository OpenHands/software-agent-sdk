"""HTTP and WebSocket ingress for owned Canvas App backends."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response, WebSocket

from openhands.agent_server.canvas_extensions.bridge import (
    AppBackendSessionResponse,
    create_app_backend_session,
    delete_app_backend_session,
    proxy_app_backend_http,
    proxy_app_backend_websocket,
)
from openhands.agent_server.canvas_extensions_router import (
    CANVAS_EXTENSION_NAME_PATTERN,
)
from openhands.agent_server.dependencies import check_session_api_key


app_backend_bridge_router = APIRouter(tags=["Canvas App Backends"])
AppBackendNamePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=255,
        pattern=CANVAS_EXTENSION_NAME_PATTERN,
        description="Installed Canvas App name",
    ),
]


@app_backend_bridge_router.post(
    "/app-backends/{extension_name}/session",
    response_model=AppBackendSessionResponse,
    dependencies=[Depends(check_session_api_key)],
    responses={
        401: {"description": "Missing or invalid X-Session-API-Key header"},
        403: {"description": "Origin is not allowed"},
        409: {"description": "Ingress does not use a separate browser origin"},
        503: {"description": "Backend is unavailable or ingress is not configured"},
    },
)
async def create_app_backend_session_endpoint(
    extension_name: AppBackendNamePath,
    request: Request,
    response: Response,
) -> AppBackendSessionResponse:
    """Issue a short-lived HttpOnly session for one ready app backend.

    Call this endpoint on the configured app ingress origin, using the normal
    ``X-Session-API-Key`` control header and the Canvas page's ``Origin``. The
    returned ingress URL contains no credential. The caller must retain cookies
    (``credentials: include``); third-party iframe deployments require HTTPS and
    browser support for partitioned cookies.
    """
    return await create_app_backend_session(request, response, extension_name)


@app_backend_bridge_router.delete(
    "/app-backends/{extension_name}/session",
    status_code=204,
    dependencies=[Depends(check_session_api_key)],
    responses={
        204: {"description": "Browser session revoked and cookie cleared"},
        401: {"description": "Missing or invalid X-Session-API-Key header"},
        403: {"description": "Origin is not allowed"},
    },
)
async def delete_app_backend_session_endpoint(
    extension_name: AppBackendNamePath,
    request: Request,
    response: Response,
) -> Response:
    """Revoke the current app-scoped browser session."""
    await delete_app_backend_session(request, response, extension_name)
    response.status_code = 204
    return response


@app_backend_bridge_router.api_route(
    "/app-backends/{extension_name}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
@app_backend_bridge_router.api_route(
    "/app-backends/{extension_name}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def proxy_app_backend_http_endpoint(
    extension_name: AppBackendNamePath,
    request: Request,
    path: str = "",
) -> Response:
    return await proxy_app_backend_http(request, extension_name, path)


@app_backend_bridge_router.websocket("/app-backends/{extension_name}")
@app_backend_bridge_router.websocket("/app-backends/{extension_name}/{path:path}")
async def proxy_app_backend_websocket_endpoint(
    extension_name: AppBackendNamePath,
    websocket: WebSocket,
    path: str = "",
) -> None:
    await proxy_app_backend_websocket(websocket, extension_name, path)
