"""MCP (Model Context Protocol) integration for agent-sdk."""

from typing import TYPE_CHECKING

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import (
    MCPAuthCredential,
    MCPOAuthAuthCredential,
    MCPOAuthAuthentication,
    MCPOAuthState,
    MCPOAuthStateResponse,
    MCPServer,
    to_fastmcp_mcp_config,
)
from openhands.sdk.mcp.exceptions import MCPError, MCPTimeoutError


if TYPE_CHECKING:
    from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation
    from openhands.sdk.mcp.tool import MCPToolDefinition, MCPToolExecutor
    from openhands.sdk.mcp.utils import MCPToolProvider, create_mcp_tools


def __getattr__(name: str):
    if name in {"MCPToolAction", "MCPToolObservation"}:
        from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation

        exports = {
            "MCPToolAction": MCPToolAction,
            "MCPToolObservation": MCPToolObservation,
        }
    elif name in {"MCPToolDefinition", "MCPToolExecutor"}:
        from openhands.sdk.mcp.tool import MCPToolDefinition, MCPToolExecutor

        exports = {
            "MCPToolDefinition": MCPToolDefinition,
            "MCPToolExecutor": MCPToolExecutor,
        }
    elif name in {"MCPToolProvider", "create_mcp_tools"}:
        from openhands.sdk.mcp.utils import MCPToolProvider, create_mcp_tools

        exports = {
            "MCPToolProvider": MCPToolProvider,
            "create_mcp_tools": create_mcp_tools,
        }
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = exports[name]
    globals()[name] = value
    return value


__all__ = [
    "MCPClient",
    "MCPAuthCredential",
    "MCPOAuthAuthCredential",
    "MCPOAuthAuthentication",
    "MCPOAuthState",
    "MCPOAuthStateResponse",
    "MCPServer",
    "MCPToolDefinition",
    "MCPToolAction",
    "MCPToolObservation",
    "MCPToolExecutor",
    "MCPToolProvider",
    "create_mcp_tools",
    "to_fastmcp_mcp_config",
    "MCPError",
    "MCPTimeoutError",
]
