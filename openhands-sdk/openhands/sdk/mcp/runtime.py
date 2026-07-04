"""Runtime helpers for creating MCP clients from SDK MCP server settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.mcp.utils import create_mcp_tools


class MCPToolProvider(Protocol):
    """Runtime-only MCP tool materializer.

    Implementations may attach host-specific transport/auth behavior, but the
    server map passed across this SDK boundary is always the typed SDK MCP
    DataModel shape rather than a FastMCP storage primitive.
    """

    def create_tools(
        self, mcp_servers: dict[str, MCPServer], timeout: float = 30.0
    ) -> MCPClient: ...


@dataclass(frozen=True)
class DefaultMCPToolProvider:
    def create_tools(
        self, mcp_servers: dict[str, MCPServer], timeout: float = 30.0
    ) -> MCPClient:
        return create_mcp_tools(mcp_servers, timeout)
