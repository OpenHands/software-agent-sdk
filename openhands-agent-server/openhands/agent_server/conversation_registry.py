"""Select and manage the configured conversation runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter

from openhands.agent_server.config import Config
from openhands.agent_server.models import (
    ConversationRuntimeInfo,
    ConversationRuntimeStatus,
)


if TYPE_CHECKING:
    from openhands.agent_server.conversation_service import ConversationService


class ConversationRegistry:
    """Route and lifecycle adapter for host-local conversations."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def configure_service(self, service: ConversationService) -> None:
        """Connect runtime-specific persistence to the shared catalog."""

    def runtime_info(self, _conversation_id: UUID) -> ConversationRuntimeInfo:
        """Describe whether a catalog conversation has an executable runtime."""
        return ConversationRuntimeInfo(
            runtime_status=ConversationRuntimeStatus.AVAILABLE,
            can_resume=True,
        )

    @property
    def serves_persisted_event_reads(self) -> bool:
        """Whether read-only event routes should use shared persisted storage."""
        return False

    async def start(self) -> None:
        """Start resources owned by this registry."""

    async def shutdown(self) -> None:
        """Stop resources owned by this registry."""

    def add_execution_routes(self, router: APIRouter) -> None:
        from openhands.agent_server.event_router import event_router
        from openhands.agent_server.runtime_router import create_runtime_router

        router.include_router(create_runtime_router())
        router.include_router(event_router)

    @property
    def workspace_router(self) -> APIRouter:
        from openhands.agent_server.workspace_router import workspace_router

        return workspace_router

    @property
    def conversation_sockets_router(self) -> APIRouter:
        from openhands.agent_server.sockets import conversation_sockets_router

        return conversation_sockets_router

    @property
    def session_sockets_router(self) -> APIRouter:
        from openhands.agent_server.session_socket import session_router

        return session_router

    @property
    def sockets_router(self) -> APIRouter:
        from openhands.agent_server.sockets import bash_sockets_router

        router = APIRouter()
        router.include_router(self.conversation_sockets_router)
        router.include_router(self.session_sockets_router)
        router.include_router(bash_sockets_router)
        return router


def create_conversation_registry(config: Config) -> ConversationRegistry:
    if config.conversation_runtime == "docker":
        from openhands.agent_server.docker_runtime.registry import (
            DockerConversationRegistry,
        )

        return DockerConversationRegistry(config)
    return ConversationRegistry(config)
