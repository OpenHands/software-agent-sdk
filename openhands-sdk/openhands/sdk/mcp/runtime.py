"""Runtime helpers for creating MCP clients from OpenHands agent config."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from key_value.aio.protocols import AsyncKeyValue

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.utils import create_mcp_tools


MCPOAuthTokenStorageFactory = Callable[[], AsyncKeyValue]


@dataclass(frozen=True)
class MCPRuntimeConfig:
    """Runtime-only MCP dependencies excluded from serialized agent config."""

    oauth_token_storage_factory: MCPOAuthTokenStorageFactory | None = None

    def create_tools(self, config: dict[str, Any], timeout: float = 30.0) -> MCPClient:
        token_storage = (
            self.oauth_token_storage_factory()
            if self.oauth_token_storage_factory is not None
            else None
        )
        return create_mcp_tools(
            config,
            timeout,
            mcp_oauth_token_storage=token_storage,
        )
