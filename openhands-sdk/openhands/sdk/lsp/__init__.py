"""LSP (Language Server Protocol) integration for OpenHands SDK.

This module provides runtime support for LSP servers configured in plugins/marketplace,
enabling agents to leverage code intelligence capabilities like go-to-definition,
find-references, and hover documentation.

Example usage:
    >>> from openhands.sdk.lsp import create_lsp_tools
    >>>
    >>> tools = create_lsp_tools({
    ...     "lspServers": {
    ...         "typescript": {
    ...             "command": "typescript-language-server",
    ...             "args": ["--stdio"],
    ...             "extensionToLanguage": {
    ...                 ".ts": "typescript",
    ...                 ".tsx": "typescriptreact",
    ...             }
    ...         }
    ...     }
    ... }, workspace_root="/path/to/project")
"""

from openhands.sdk.lsp.client import LSPClient
from openhands.sdk.lsp.definition import (
    LSPOperation,
    LSPToolAction,
    LSPToolObservation,
)
from openhands.sdk.lsp.exceptions import (
    LSPConnectionError,
    LSPError,
    LSPServerError,
    LSPServerNotFoundError,
    LSPTimeoutError,
)
from openhands.sdk.lsp.manager import LSPServerConfig, LSPServerManager
from openhands.sdk.lsp.tool import LSPToolDefinition, LSPToolExecutor
from openhands.sdk.lsp.utils import create_lsp_tools, validate_lsp_config


__all__ = [
    # Client
    "LSPClient",
    # Manager
    "LSPServerConfig",
    "LSPServerManager",
    # Tool
    "LSPToolDefinition",
    "LSPToolExecutor",
    # Schemas
    "LSPOperation",
    "LSPToolAction",
    "LSPToolObservation",
    # Exceptions
    "LSPError",
    "LSPTimeoutError",
    "LSPServerError",
    "LSPServerNotFoundError",
    "LSPConnectionError",
    # Utils
    "create_lsp_tools",
    "validate_lsp_config",
]
