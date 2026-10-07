"""Serve host-local and Docker conversations from one server.

Local routes keep their public paths. Docker routes are mounted again under
``DOCKER_ROUTE_PREFIX`` and :class:`RuntimeDispatchMiddleware` rewrites a
request onto them when its conversation has a Docker provisioning manifest, or
when a start request selects ``conversation_runtime="docker"``.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openhands.agent_server.conversation_registry import ConversationRegistry
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry
from openhands.agent_server.models import ConversationRuntimeInfo


DOCKER_ROUTE_PREFIX = "/_docker"

_UUID = (
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
)
_CONVERSATION_PATH = re.compile(rf"^/api/conversations/({_UUID})(/.*)?$")
_SOCKET_PATH = re.compile(rf"^/sockets/(?:events|session)/({_UUID})$")


class HybridConversationRegistry(DockerConversationRegistry):
    def is_docker(self, conversation_id: UUID) -> bool:
        return self.provisioning.manifest_path(conversation_id).is_file()

    def runtime_info(self, conversation_id: UUID) -> ConversationRuntimeInfo:
        if self.is_docker(conversation_id):
            return super().runtime_info(conversation_id)
        return ConversationRegistry.runtime_info(self, conversation_id)

    @property
    def serves_persisted_event_reads(self) -> bool:
        return False

    def serves_persisted_event_reads_for(self, conversation_id: UUID) -> bool:
        return self.is_docker(conversation_id)

    def add_execution_routes(self, router: APIRouter) -> None:
        ConversationRegistry.add_execution_routes(self, router)
        docker = APIRouter(prefix=DOCKER_ROUTE_PREFIX, include_in_schema=False)
        super().add_execution_routes(docker)
        router.include_router(docker)

    @property
    def workspace_router(self) -> APIRouter:
        from openhands.agent_server.workspace_router import workspace_router

        router = APIRouter()
        router.include_router(workspace_router)
        router.include_router(
            super().workspace_router,
            prefix=DOCKER_ROUTE_PREFIX,
            include_in_schema=False,
        )
        return router

    @property
    def sockets_router(self) -> APIRouter:
        from openhands.agent_server.docker_runtime.routers import (
            docker_session_sockets_router,
            docker_sockets_router,
        )
        from openhands.agent_server.session_socket import session_router
        from openhands.agent_server.sockets import (
            bash_sockets_router,
            conversation_sockets_router,
        )

        router = APIRouter()
        router.include_router(conversation_sockets_router)
        router.include_router(session_router)
        router.include_router(bash_sockets_router)
        for docker in (docker_sockets_router, docker_session_sockets_router):
            router.include_router(
                docker, prefix=DOCKER_ROUTE_PREFIX, include_in_schema=False
            )
        return router


class RuntimeDispatchMiddleware:
    """Route each conversation's requests to the runtime that owns it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        registry = scope["app"].state.conversation_registry if "app" in scope else None
        if scope["type"] not in ("http", "websocket") or not isinstance(
            registry, HybridConversationRegistry
        ):
            await self.app(scope, receive, send)
            return

        root_path = scope.get("root_path", "")
        path = scope["path"].removeprefix(root_path)
        method = scope.get("method")
        to_docker = False
        if scope["type"] == "websocket":
            match = _SOCKET_PATH.match(path)
            to_docker = match is not None and registry.is_docker(UUID(match[1]))
        elif path == "/api/conversations" and method == "POST":
            receive, body = await _buffer_body(receive)
            to_docker = (
                _requested_runtime(body) or registry.config.conversation_runtime
            ) == "docker"
        elif match := _CONVERSATION_PATH.match(path):
            # GET of the conversation itself is the shared catalog record.
            catalog_read = match[2] is None and method in ("GET", "HEAD")
            to_docker = not catalog_read and registry.is_docker(UUID(match[1]))

        if to_docker:
            prefix = "/api" if path.startswith("/api/") else ""
            rewritten = prefix + DOCKER_ROUTE_PREFIX + path[len(prefix) :]
            full = root_path + rewritten
            scope = {**scope, "path": full, "raw_path": full.encode()}
        await self.app(scope, receive, send)


async def _buffer_body(receive: Receive) -> tuple[Receive, bytes]:
    messages: list[Message] = []
    body = b""
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        body += message.get("body", b"")
        if not message.get("more_body"):
            break

    async def replay() -> Message:
        return messages.pop(0) if messages else await receive()

    return replay, body


def _requested_runtime(body: bytes) -> Any:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data.get("conversation_runtime") if isinstance(data, dict) else None
