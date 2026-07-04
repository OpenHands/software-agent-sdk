"""MCP (Model Context Protocol) integration for agent-sdk."""

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import (
    MCPAuthCredential,
    MCPOAuthAuthCredential,
    MCPServer,
    MCPServers,
    to_fastmcp_mcp_config,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation
from openhands.sdk.mcp.exceptions import MCPError, MCPTimeoutError
from openhands.sdk.mcp.runtime import DefaultMCPToolProvider, MCPToolProvider
from openhands.sdk.mcp.tool import (
    MCPToolDefinition,
    MCPToolExecutor,
)
from openhands.sdk.mcp.utils import (
    create_mcp_tools,
)


__all__ = [
    "MCPClient",
    "MCPAuthCredential",
    "MCPOAuthAuthCredential",
    "MCPServers",
    "MCPServer",
    "MCPToolDefinition",
    "MCPToolAction",
    "MCPToolObservation",
    "MCPToolExecutor",
    "DefaultMCPToolProvider",
    "MCPToolProvider",
    "create_mcp_tools",
    "to_fastmcp_mcp_config",
    "MCPError",
    "MCPTimeoutError",
]
