"""FastAPI routes that intercept conversation-mutation traffic in
``Config.conversation_runtime == "docker"`` mode.

What this module provides (the *new* surface):

* ``docker_conversation_proxy_router`` — spawns a per-conversation
  container on ``POST /api/conversations`` and forwards every per-cid
  HTTP route to that container. Mounted BEFORE
  :data:`openhands.agent_server.conversation_router.conversation_router`
  so the proxy claims the mutation methods first; the unchanged
  ``conversation_router`` keeps serving ``GET`` metadata routes from the
  shared on-disk persistence dir (the outer's
  :class:`ConversationService` runs in :attr:`read_only_metadata` mode).
* ``docker_sockets_router`` — authenticates WebSocket clients against
  the outer's session keys, then bridges to the inner container.
* ``docker_global_proxy_router`` — reverse-proxies the *global*
  (non-conversation-scoped) routers (bash, file, git, vscode, desktop,
  hooks, mcp, skills, tool, llm) to a chosen sub-container. Each request
  must include a ``?cid=…`` query param identifying which conversation's
  container to talk to.

What's intentionally NOT here:

* No batch-get / count / search routes — those are served by the
  unchanged ``conversation_router`` against the shared filesystem.
* No workspace static router — the catch-all
  ``/{cid}/{tail:path}`` proxy covers ``/{cid}/workspace/...`` already,
  and the outer mounts a thin cookie-auth wrapper that delegates here.
"""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    WebSocket,
    status,
)
from starlette.responses import JSONResponse, Response, StreamingResponse

from openhands.agent_server.docker_runtime.proxy import (
    bridge_websocket,
    proxy_http,
)
from openhands.agent_server.docker_runtime.registry import (
    DockerConversationRegistry,
)
from openhands.sdk.logger import get_logger
from openhands.workspace.docker.workspace import DockerWorkspace


logger = get_logger(__name__)


def get_registry(request: Request) -> DockerConversationRegistry:
    registry = getattr(request.app.state, "docker_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Docker conversation registry is not available",
        )
    return registry


def _ws_get_registry(websocket: WebSocket) -> DockerConversationRegistry | None:
    return getattr(websocket.app.state, "docker_registry", None)


def _workspace_or_404(
    registry: DockerConversationRegistry, conversation_id: UUID
) -> DockerWorkspace:
    ws = registry.get(conversation_id)
    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )
    return ws


def _build_upstream_path(request: Request, path: str) -> str:
    """Reconstruct the inner-container path from the outer request.

    The inner agent-server exposes the same API surface, so we forward the
    same path verbatim and append the original query string.
    """
    query = request.url.query
    return f"{path}?{query}" if query else path


# ---------------------------------------------------------------------------
# HTTP: /api/conversations (mutation half)
# ---------------------------------------------------------------------------

docker_conversation_proxy_router = APIRouter(
    prefix="/conversations", tags=["Docker Conversations"]
)


@docker_conversation_proxy_router.post("")
async def docker_start_conversation(
    request: Request,
    include_skills: Annotated[bool, Query()] = False,
) -> JSONResponse:
    """Spawn a fresh per-conversation container, then forward the create.

    The container is registered against the *resolved* conversation id
    (either the one the client supplied or a fresh UUID4 minted here).
    The body is rewritten to pin ``conversation_id`` so the inner
    agent-server agrees on the id.
    """
    registry = get_registry(request)

    try:
        body_bytes = await request.body()
        body = json.loads(body_bytes) if body_bytes else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON body: {exc}",
        ) from exc

    raw_cid = body.get("conversation_id")
    try:
        conversation_id = UUID(raw_cid) if raw_cid else uuid4()
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid conversation_id: {raw_cid!r}",
        ) from exc
    body["conversation_id"] = str(conversation_id)

    try:
        workspace, is_new = await registry.get_or_create(conversation_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to start conversation container: {exc}",
        ) from exc

    upstream_path = (
        f"/api/conversations?include_skills={'true' if include_skills else 'false'}"
    )
    headers = {
        "content-type": request.headers.get("content-type", "application/json"),
        "accept": request.headers.get("accept", "application/json"),
    }
    # Forward auth: prefer whatever the client sent (header takes precedence),
    # fall back to the workspace's stored key (set from the outer's shared
    # ``OH_SESSION_API_KEYS_0``). The inner agent-server only ever knows
    # about the header, never the cookie.
    inbound_key = request.headers.get("x-session-api-key")
    proxied_key = inbound_key or workspace.api_key
    if proxied_key:
        headers["X-Session-API-Key"] = proxied_key

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                workspace.host + upstream_path,
                headers=headers,
                content=json.dumps(body).encode("utf-8"),
            )
    except httpx.HTTPError as exc:
        # If we managed to start the container but the very first request
        # failed, that's a startup race. Tear down only the container WE
        # just created — otherwise a retry against an existing
        # conversation would kill the live one.
        logger.warning(
            "Initial request to fresh container %s failed: %s", workspace.host, exc
        )
        if is_new:
            await registry.stop(conversation_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Conversation container could not accept the request: {exc}",
        ) from exc

    if response.status_code >= 400 and is_new:
        # The inner server rejected the create. Don't leave the container
        # behind in that case.
        await registry.stop(conversation_id)

    return JSONResponse(
        content=response.json() if response.content else None,
        status_code=response.status_code,
    )


@docker_conversation_proxy_router.delete("/{conversation_id}")
async def docker_delete_conversation(
    conversation_id: UUID,
    request: Request,
) -> Response:
    registry = get_registry(request)
    workspace = registry.get(conversation_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )

    # Best-effort: ask the inner server to delete its own state first, then
    # always tear the container down so we don't leak it even if the inner
    # delete failed.
    delete_status = 200
    delete_body: bytes = b""
    delete_headers: dict[str, str] = {}
    inbound_key = request.headers.get("x-session-api-key")
    proxied_key = inbound_key or workspace.api_key
    if proxied_key:
        delete_headers["X-Session-API-Key"] = proxied_key
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.delete(
                f"{workspace.host}/api/conversations/{conversation_id}",
                headers=delete_headers,
            )
        delete_status = upstream.status_code
        delete_body = upstream.content
    except httpx.HTTPError as exc:
        logger.warning("Inner DELETE failed for %s: %s", conversation_id, exc)
    finally:
        await registry.stop(conversation_id)

    return Response(
        content=delete_body,
        status_code=delete_status,
        media_type="application/json",
    )


@docker_conversation_proxy_router.api_route(
    "/{conversation_id}",
    methods=["PATCH"],
)
async def docker_proxy_conversation_root_mutation(
    conversation_id: UUID, request: Request
) -> StreamingResponse:
    """Proxy mutating verbs on ``/api/conversations/{cid}``.

    ``GET`` is intentionally NOT included — the outer's
    ``conversation_router`` handles it locally by reading the shared
    persistence dir.
    """
    registry = get_registry(request)
    workspace = _workspace_or_404(registry, conversation_id)
    return await proxy_http(
        request,
        workspace,
        upstream_path=_build_upstream_path(
            request, f"/api/conversations/{conversation_id}"
        ),
    )


@docker_conversation_proxy_router.api_route(
    "/{conversation_id}/{tail:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def docker_proxy_conversation_subpath(
    conversation_id: UUID, tail: str, request: Request
) -> StreamingResponse:
    """Catch-all that proxies every conversation-scoped sub-route.

    Covers ``/run``, ``/pause``, ``/interrupt``, ``/secrets``,
    ``/confirmation_policy``, ``/switch_profile``, ``/switch_llm``,
    ``/condense``, ``/fork``, ``/agent_final_response``, all of
    ``/events/...``, and all of ``/workspace/...`` (static file
    server).
    """
    registry = get_registry(request)
    workspace = _workspace_or_404(registry, conversation_id)
    upstream_path = _build_upstream_path(
        request, f"/api/conversations/{conversation_id}/{tail}"
    )
    return await proxy_http(request, workspace, upstream_path=upstream_path)


# ---------------------------------------------------------------------------
# Workspace static files — same path as the local ``workspace_router``,
# but served under the workspace-cookie auth group so that <iframe> /
# <img> embeds work without an X-Session-API-Key header. Registered
# under ``workspace_api_router`` in :mod:`api`, separately from the
# header-only ``docker_conversation_proxy_router`` whose catch-all
# would otherwise shadow this with header-only auth.
# ---------------------------------------------------------------------------

docker_workspace_proxy_router = APIRouter(
    prefix="/conversations", tags=["Docker Workspace"]
)


@docker_workspace_proxy_router.get("/{conversation_id}/workspace/{file_path:path}")
async def docker_proxy_workspace_file(
    conversation_id: UUID, file_path: str, request: Request
) -> StreamingResponse:
    """Proxy workspace static-file reads to the per-conversation container.

    The local ``workspace_router`` resolves ``file_path`` against the
    conversation's working dir on the host. In docker mode the canonical
    filesystem lives inside the sub-container, so we just hand the
    request through to its identical route.
    """
    registry = get_registry(request)
    workspace = _workspace_or_404(registry, conversation_id)
    upstream_path = _build_upstream_path(
        request,
        f"/api/conversations/{conversation_id}/workspace/{file_path}",
    )
    return await proxy_http(request, workspace, upstream_path=upstream_path)


# ---------------------------------------------------------------------------
# WebSockets: /sockets/events/{cid}
# ---------------------------------------------------------------------------

docker_sockets_router = APIRouter(prefix="/sockets", tags=["Docker WebSockets"])


@docker_sockets_router.websocket("/events/{conversation_id}")
async def docker_events_websocket(
    websocket: WebSocket,
    conversation_id: UUID,
    session_api_key: Annotated[str | None, Query(alias="session_api_key")] = None,
) -> None:
    """Authenticated WebSocket bridge to the per-conversation container.

    Outer-side auth must succeed against the outer server's session keys
    BEFORE we touch the inner container. The helper accepts the same
    three auth methods the local sockets router accepts (header / query /
    first-message ``{"type": "auth", ...}``); on success it has already
    ``accept()``ed the socket, so the downstream bridge must not accept
    again.
    """
    # Imported lazily to avoid a circular import: the sockets module pulls
    # in the in-process conversation service at module scope.
    from openhands.agent_server.sockets import _accept_authenticated_websocket

    if not await _accept_authenticated_websocket(websocket, session_api_key):
        return

    registry = _ws_get_registry(websocket)
    if registry is None:
        await websocket.close(code=1011)
        return
    workspace = registry.get(conversation_id)
    if workspace is None:
        # 1008 == policy violation; closest standard code for "no such conv".
        await websocket.close(code=1008)
        return

    # Strip the auth query param before forwarding upstream — the outer's
    # session key must never leak into the inner container's request log.
    upstream_path = f"/sockets/events/{conversation_id}"
    forwarded_query = _strip_auth_query(websocket.url.query)
    if forwarded_query:
        upstream_path = f"{upstream_path}?{forwarded_query}"
    await bridge_websocket(websocket, workspace, upstream_path=upstream_path)


def _strip_auth_query(query: str) -> str:
    if not query:
        return ""
    from urllib.parse import parse_qsl, urlencode

    keep = [
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if k != "session_api_key"
    ]
    return urlencode(keep)


# ---------------------------------------------------------------------------
# HTTP: global (non-cid-scoped) routes — bash, git, file, vscode, desktop,
# hooks, mcp, skills, tool, llm. These live at fixed prefixes like ``/bash``,
# ``/git``, ``/file``, etc. In docker mode they MUST carry a ``?cid=...``
# query parameter so the outer knows which sub-container to talk to.
# ---------------------------------------------------------------------------

# Path prefixes (under ``/api``) of the routers that are global in local
# mode but conversation-scoped in docker mode. Anything else under ``/api``
# is either served locally (settings, profiles, workspaces, server_info,
# conversations metadata) or handled by ``docker_conversation_proxy_router``
# (conversation mutations).
_DOCKER_GLOBAL_PREFIXES: tuple[str, ...] = (
    "bash",
    "git",
    "file",
    "vscode",
    "desktop",
    "hooks",
    "mcp",
    "skills",
    "tools",
    "llm",
)

_GLOBAL_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _make_docker_global_handler(prefix: str):
    """Build a per-prefix proxy handler.

    Registered once per prefix in :data:`_DOCKER_GLOBAL_PREFIXES`. We
    cannot use a single ``/{tail:path}`` route because that would also
    swallow ``/api/conversations``, ``/api/settings``, etc. and shadow
    the local routers mounted afterwards on the same prefix.
    """

    async def _handler(
        tail: str,
        request: Request,
        cid: Annotated[
            UUID | None,
            Query(
                alias="cid",
                description=(
                    "Conversation id whose container should serve the request. "
                    "Required for global routers (bash / git / file / vscode / "
                    "desktop / hooks / mcp / skills / tools / llm) when "
                    "``conversation_runtime == 'docker'``."
                ),
            ),
        ] = None,
    ) -> StreamingResponse:
        if cid is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Docker mode requires a ``?cid=…`` query parameter on "
                    f"``/api/{prefix}/...`` so the outer server knows which "
                    "conversation container to forward to."
                ),
            )
        registry = get_registry(request)
        workspace = _workspace_or_404(registry, cid)
        upstream_path = _build_upstream_path(
            request, f"/api/{prefix}/{tail}" if tail else f"/api/{prefix}"
        )
        return await proxy_http(request, workspace, upstream_path=upstream_path)

    _handler.__name__ = f"docker_proxy_global_{prefix}"
    return _handler


def _make_docker_global_bare_handler(prefix: str):
    """Companion to :func:`_make_docker_global_handler` for the
    bare-prefix path (e.g. ``GET /api/bash``). Just calls the same logic
    with an empty tail."""
    tail_handler = _make_docker_global_handler(prefix)

    async def _handler(
        request: Request,
        cid: Annotated[UUID | None, Query(alias="cid")] = None,
    ) -> StreamingResponse:
        return await tail_handler("", request, cid)

    _handler.__name__ = f"docker_proxy_global_{prefix}_bare"
    return _handler


docker_global_proxy_router = APIRouter(tags=["Docker Global Proxy"])
for _prefix in _DOCKER_GLOBAL_PREFIXES:
    docker_global_proxy_router.add_api_route(
        f"/{_prefix}/{{tail:path}}",
        _make_docker_global_handler(_prefix),
        methods=_GLOBAL_PROXY_METHODS,
    )
    docker_global_proxy_router.add_api_route(
        f"/{_prefix}",
        _make_docker_global_bare_handler(_prefix),
        methods=_GLOBAL_PROXY_METHODS,
    )
