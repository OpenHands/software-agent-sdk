"""Runtime helpers for creating MCP clients from SDK MCP config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPConfig
from openhands.sdk.mcp.utils import create_mcp_tools


class MCPToolProvider(Protocol):
    """Runtime-only MCP tool materializer.

    Implementations may attach host-specific transport/auth behavior, but the
    config passed across this SDK boundary is always the SDK MCP
    DataModel rather than an untyped dict or a FastMCP storage primitive.
    """

    def create_tools(self, config: MCPConfig, timeout: float = 30.0) -> MCPClient: ...


@dataclass(frozen=True)
class DefaultMCPToolProvider:
    def create_tools(self, config: MCPConfig, timeout: float = 30.0) -> MCPClient:
        return create_mcp_tools(config, timeout)
