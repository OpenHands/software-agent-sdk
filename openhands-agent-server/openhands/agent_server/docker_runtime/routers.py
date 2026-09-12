"""Docker conversation creation, recovery, and HTTP/WebSocket proxy routes.

Mutation routes precede local conversation routes; metadata reads stay on the
outer server. Legacy runtime endpoints select a container with ``?cid=``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    status,
)
from starlette.responses import JSONResponse, Response, StreamingResponse

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.docker_runtime.mediation import (
    grants_for_agent,
    has_auxiliary_subscription,
    materialize_start,
    materialize_title_profile,
    mediate_mutation,
    serialize_for_runtime,
)
from openhands.agent_server.docker_runtime.proxy import (
    bridge_websocket,
    proxy_http,
    strip_auth_query,
)
from openhands.agent_server.docker_runtime.registry import (
    DockerConversationRegistry,
    RunningConversationContainer,
)
from openhands.agent_server.models import ConversationInfo, ConversationRuntimeInfo
from openhands.agent_server.runtime_router import add_legacy_runtime_routes
from openhands.agent_server.utils import safe_rmtree
from openhands.sdk.logger import get_logger


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


def _is_archived(directory: Path) -> bool:
    return (
        json.loads((directory / "meta.json").read_text()).get("archived_at") is not None
    )


async def _workspace_or_404(
    registry: DockerConversationRegistry, conversation_id: UUID
) -> RunningConversationContainer:
    ws = registry.get(conversation_id)
    if ws is not None:
        return ws
    directory = registry.conversation_dir(conversation_id)
    if (
        not (directory / "meta.json").is_file()
        or not (directory / "base_state.json").is_file()
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )
    if _is_archived(directory):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation is archived; unarchive it before using its runtime",
        )
    try:
        registry.provisioning.load(conversation_id)
    except ValueError as exc:
        raise HTTPException(
            409, "Legacy runtime cannot be resumed; create a new isolated conversation"
        ) from exc
    try:
        await registry.prepare(conversation_id)
        ws, _ = await registry.get_or_create(conversation_id)
    except Exception as exc:
        logger.exception("Could not recover conversation %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not recover conversation container",
        ) from exc
    return ws


def _build_upstream_path(request: Request, path: str) -> str:
    """Reconstruct the inner-container path from the outer request.

    The inner agent-server exposes the same API surface, so we forward the
    same path verbatim and append the original query string.
    """
    query = strip_auth_query("?" + request.url.query).lstrip("?")
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

    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Expected a JSON object")
    selected_workspace = body.get("workspace")
    if selected_workspace is not None and (
        not isinstance(selected_workspace, dict)
        or selected_workspace.get("working_dir", "/workspace") != "/workspace"
        or selected_workspace.get("kind", "LocalWorkspace") != "LocalWorkspace"
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Docker conversations use a new isolated /workspace. Host project "
                "paths cannot be mounted or copied through this API. Omit workspace "
                "to explicitly create an isolated workspace, or use local runtime "
                "to work on a host project."
            ),
        )

    raw_cid = body.get("conversation_id")
    try:
        conversation_id = UUID(raw_cid) if raw_cid else uuid4()
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid conversation_id: {raw_cid!r}",
        ) from exc
    body["conversation_id"] = str(conversation_id)
    body["workspace"] = {
        "kind": "LocalWorkspace",
        "working_dir": "/workspace",
    }

    async with registry.mutation_lock(conversation_id):
        return await _start_prepared_conversation(
            request, registry, body, conversation_id, include_skills
        )


async def _start_prepared_conversation(
    request: Request,
    registry: DockerConversationRegistry,
    body: dict,
    conversation_id: UUID,
    include_skills: bool,
) -> JSONResponse:
    try:
        resolved, launched = await materialize_start(body, registry.config)
    except ValueError as exc:
        raise HTTPException(
            422, "Invalid runtime configuration or unsupported secret reference"
        ) from exc
    try:
        identity = registry.provisioning.create(conversation_id)
    except ValueError as exc:
        raise HTTPException(
            409, "Legacy runtime cannot be resumed; create a new isolated conversation"
        ) from exc
    original_identity = identity
    existing_state = (
        registry.conversation_dir(conversation_id) / "base_state.json"
    ).exists()
    identity = identity.model_copy(
        update={
            "grants": grants_for_agent(resolved.agent),
            "launched_agent_profile": launched,
            "auxiliary_subscription": has_auxiliary_subscription(resolved),
        }
    )
    if identity.auxiliary_subscription:
        identity = identity.model_copy(
            update={
                "grants": identity.grants.model_copy(update={"subscription": "openai"})
            }
        )
    if existing_state:
        identity = original_identity
    else:
        identity = await materialize_title_profile(
            resolved, registry.provisioning, identity
        )
    registry.provisioning.save(identity)
    body = serialize_for_runtime(resolved, identity)
    try:
        await registry.prepare(conversation_id)
        workspace, is_new = await registry.get_or_create(conversation_id)
    except Exception as exc:
        registry.provisioning.save(original_identity)
        await registry.stop(conversation_id)
        raise HTTPException(502, "Failed to start conversation container") from exc

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
    proxied_key = workspace.api_key
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
        logger.warning("Initial request to conversation container failed")
        registry.provisioning.save(original_identity)
        if is_new:
            await registry.stop(conversation_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Conversation container could not accept the request",
        ) from exc

    if response.status_code >= 400:
        registry.provisioning.save(original_identity)
    if response.status_code >= 400 and is_new:
        # The inner server rejected the create. Don't leave the container
        # behind in that case.
        await registry.stop(conversation_id)

    return JSONResponse(
        content=(
            {"detail": "Conversation runtime rejected the request"}
            if response.is_error
            else response.json()
            if response.content
            else None
        ),
        status_code=response.status_code,
    )


@docker_conversation_proxy_router.delete("/{conversation_id}")
async def docker_delete_conversation(
    conversation_id: UUID,
    request: Request,
) -> Response:
    registry = get_registry(request)
    workspace = await _workspace_or_404(registry, conversation_id)

    # Best-effort: ask the inner server to delete its own state first, then
    # always tear the container down so we don't leak it even if the inner
    # delete failed.
    delete_status = 200
    delete_body: bytes = b""
    delete_headers: dict[str, str] = {}
    proxied_key = workspace.api_key
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
        registry.provisioning.manifest_path(conversation_id).unlink(missing_ok=True)
        await asyncio.to_thread(
            safe_rmtree, registry.provisioning.runtime_dir(conversation_id)
        )
        await asyncio.to_thread(
            safe_rmtree,
            registry.conversation_dir(conversation_id),
            f"conversation directory for {conversation_id}",
        )
        await asyncio.to_thread(
            safe_rmtree,
            registry.workspace_dir(conversation_id),
            f"workspace directory for {conversation_id}",
        )

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
    workspace = await _workspace_or_404(registry, conversation_id)
    return await proxy_http(
        request,
        workspace,
        upstream_path=_build_upstream_path(
            request, f"/api/conversations/{conversation_id}"
        ),
    )


@docker_conversation_proxy_router.get(
    "/{conversation_id}/runtime",
    response_model=ConversationRuntimeInfo,
)
async def get_conversation_runtime(
    conversation_id: UUID, request: Request
) -> ConversationRuntimeInfo:
    """Inspect runtime availability without provisioning a container."""
    registry = get_registry(request)
    info = registry.runtime_info(conversation_id)
    if not registry.conversation_dir(conversation_id).joinpath("meta.json").is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )
    return info


@docker_conversation_proxy_router.post(
    "/{conversation_id}/runtime/reprovision",
    response_model=ConversationRuntimeInfo,
)
async def reprovision_conversation_runtime(
    conversation_id: UUID, request: Request
) -> ConversationRuntimeInfo:
    """Start missing infrastructure without resuming agent execution."""
    registry = get_registry(request)
    info = registry.runtime_info(conversation_id)
    directory = registry.conversation_dir(conversation_id)
    if not directory.joinpath("meta.json").is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )
    if _is_archived(directory):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation is archived; unarchive it before reprovisioning",
        )
    if not info.can_resume:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation runtime cannot be safely reprovisioned",
        )
    try:
        await registry.prepare(conversation_id)
        await registry.get_or_create(conversation_id)
    except Exception as exc:
        logger.exception("Could not reprovision conversation %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reprovision conversation runtime",
        ) from exc
    return registry.runtime_info(conversation_id)


async def _set_docker_archive_state(
    conversation_id: UUID,
    request: Request,
    conversation_service: ConversationService,
    *,
    archived: bool,
) -> ConversationInfo:
    registry = get_registry(request)
    conversation = await conversation_service.set_conversation_archived(
        conversation_id, archived=archived
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )
    if archived:
        await registry.stop(conversation_id)
    lifecycle = registry.runtime_info(conversation_id)
    return conversation.model_copy(update=lifecycle.model_dump())


@docker_conversation_proxy_router.post(
    "/{conversation_id}/archive", response_model=ConversationInfo
)
async def archive_docker_conversation(
    conversation_id: UUID,
    request: Request,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationInfo:
    """Archive outer metadata and reap only a runtime owned by this server."""
    return await _set_docker_archive_state(
        conversation_id, request, conversation_service, archived=True
    )


@docker_conversation_proxy_router.post(
    "/{conversation_id}/unarchive", response_model=ConversationInfo
)
async def unarchive_docker_conversation(
    conversation_id: UUID,
    request: Request,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationInfo:
    """Restore catalog visibility without provisioning a runtime."""
    return await _set_docker_archive_state(
        conversation_id, request, conversation_service, archived=False
    )


@docker_conversation_proxy_router.api_route(
    "/{conversation_id}/{tail:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def docker_proxy_conversation_subpath(
    conversation_id: UUID, tail: str, request: Request
) -> Response:
    """Catch-all that proxies every conversation-scoped sub-route.

    Covers ``/run``, ``/pause``, ``/interrupt``, ``/secrets``,
    ``/confirmation_policy``, ``/switch_profile``, ``/switch_llm``,
    ``/condense``, ``/fork``, ``/agent_final_response``, all of
    ``/events/...``, and all of ``/workspace/...`` (static file
    server).
    """
    if tail.split("/", 1)[0] in {"fork", "children", "credential-bindings"}:
        raise HTTPException(
            501, "This operation is unsupported in isolated Docker runtimes"
        )
    if request.headers.get("x-expose-secrets"):
        raise HTTPException(
            422, "Secret exposure through runtime routes is unsupported"
        )
    allowed_mutations = {
        "run",
        "pause",
        "interrupt",
        "events",
        "confirmation_policy",
        "security_analyzer",
        "switch_profile",
        "switch_llm",
        "secrets",
        "condense",
        "ask_agent",
        "goal",
        "acp",
        "load_plugin",
    }
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and tail.split("/", 1)[0] not in allowed_mutations
    ):
        raise HTTPException(
            501, "This runtime mutation requires explicit boundary support"
        )
    registry = get_registry(request)
    workspace = await _workspace_or_404(registry, conversation_id)
    if tail in {"switch_profile", "switch_llm", "secrets", "security_analyzer"}:
        async with registry.mutation_lock(conversation_id):
            identity = registry.provisioning.load(conversation_id)
            try:
                target, payload, grants = await mediate_mutation(
                    tail, await request.json(), registry.config, identity
                )
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(
                    422, "Invalid selected runtime configuration"
                ) from exc
            if grants is not None and grants.subscription:
                pending = identity.grants.model_copy(
                    update={"subscription": grants.subscription}
                )
                registry.provisioning.save(
                    identity.model_copy(update={"grants": pending})
                )
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.request(
                        request.method,
                        f"{workspace.host}/api/conversations/{conversation_id}/{target}",
                        headers={"X-Session-API-Key": workspace.api_key or ""},
                        json=payload,
                    )
                committed = (
                    identity.model_copy(update={"grants": grants})
                    if response.is_success and grants is not None
                    else identity
                )
                registry.provisioning.save(committed)
            except BaseException:
                registry.provisioning.save(identity)
                raise
            return JSONResponse(
                {"detail": "Runtime mutation rejected"}
                if response.is_error
                else response.json(),
                status_code=response.status_code,
            )
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
    workspace = await _workspace_or_404(registry, conversation_id)
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
    try:
        workspace = await _workspace_or_404(registry, conversation_id)
    except HTTPException as exc:
        await websocket.close(code=1008 if exc.status_code == 404 else 1011)
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
# These live at fixed prefixes like ``/bash``,
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
)


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
                    "desktop) when "
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
        workspace = await _workspace_or_404(registry, cid)
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
    add_legacy_runtime_routes(
        docker_global_proxy_router,
        _prefix,
        _make_docker_global_handler(_prefix),
        _make_docker_global_bare_handler(_prefix),
    )
