"""MCP (Model Context Protocol) integration for agent-sdk."""

from typing import Any

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation
from openhands.sdk.mcp.exceptions import MCPError, MCPTimeoutError
from openhands.sdk.mcp.tool import (
    MCPToolDefinition,
    MCPToolExecutor,
)
from openhands.sdk.mcp.utils import (
    create_mcp_tools,
)


# Config utilities are lazily exported to avoid circular imports:
# skills/skill.py → mcp/__init__ → mcp/definition → tool → skills
_CONFIG_NAMES = {
    "SecretLookup",
    "find_mcp_config",
    "expand_mcp_variables",
    "load_mcp_config",
    "merge_mcp_configs",
}


def __getattr__(name: str) -> Any:
    if name in _CONFIG_NAMES:
        from openhands.sdk.mcp import config as _cfg

        return getattr(_cfg, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MCPClient",
    "MCPToolDefinition",
    "MCPToolAction",
    "MCPToolObservation",
    "MCPToolExecutor",
    "create_mcp_tools",
    "MCPError",
    "MCPTimeoutError",
    # Config utilities (lazy)
    "SecretLookup",
    "find_mcp_config",
    "expand_mcp_variables",
    "load_mcp_config",
    "merge_mcp_configs",
]
