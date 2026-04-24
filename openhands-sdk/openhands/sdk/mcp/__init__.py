"""MCP (Model Context Protocol) integration for agent-sdk."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


if TYPE_CHECKING:
    from .client import MCPClient
    from .definition import MCPToolAction, MCPToolObservation
    from .exceptions import MCPError, MCPTimeoutError
    from .tool import MCPToolDefinition, MCPToolExecutor
    from .utils import create_mcp_tools


__all__ = [
    "MCPClient",
    "MCPToolDefinition",
    "MCPToolAction",
    "MCPToolObservation",
    "MCPToolExecutor",
    "create_mcp_tools",
    "MCPError",
    "MCPTimeoutError",
]

_LAZY_IMPORTS = {
    "MCPClient": (".client", "MCPClient"),
    "MCPToolDefinition": (".tool", "MCPToolDefinition"),
    "MCPToolAction": (".definition", "MCPToolAction"),
    "MCPToolObservation": (".definition", "MCPToolObservation"),
    "MCPToolExecutor": (".tool", "MCPToolExecutor"),
    "create_mcp_tools": (".utils", "create_mcp_tools"),
    "MCPError": (".exceptions", "MCPError"),
    "MCPTimeoutError": (".exceptions", "MCPTimeoutError"),
}


def __getattr__(name: str) -> Any:
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
