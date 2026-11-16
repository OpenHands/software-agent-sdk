"""Inspect managed storage without extending the server's activity lifetime."""

from typing import Annotated

from fastapi import APIRouter, Depends

from openhands.agent_server.conversation_registry import ConversationRegistry
from openhands.agent_server.dependencies import get_conversation_registry
from openhands.agent_server.storage.status import StorageStatus


storage_router = APIRouter(prefix="/storage", tags=["Storage"])


@storage_router.get("")
async def get_storage_status(
    registry: Annotated[ConversationRegistry, Depends(get_conversation_registry)],
) -> StorageStatus:
    """Return backing filesystem capacity, not per-conversation disk usage.

    Local workspaces are caller-owned and are not measured. In mixed-runtime
    mode, only managed Docker storage is represented. This does not scan files,
    reclaim storage, start containers, or count as user activity.
    """
    return await registry.storage_status()
