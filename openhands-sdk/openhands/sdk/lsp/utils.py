"""Utility functions for LSP integration."""

from typing import Any

from openhands.sdk.logger import get_logger
from openhands.sdk.lsp.tool import LSPToolDefinition


logger = get_logger(__name__)


def create_lsp_tools(
    config: dict[str, Any],
    workspace_root: str,
    timeout: float = 30.0,  # noqa: ARG001
) -> list[LSPToolDefinition]:
    """Create LSP tools from configuration.

    Similar to create_mcp_tools() in openhands/sdk/mcp/utils.py.

    Args:
        config: LSP configuration dictionary with format:
            {"lspServers": {"name": {"command": "...", "args": [...], ...}}}
            or {"servers": {"name": {...}}}
        workspace_root: Root directory of the workspace
        timeout: Default timeout for LSP operations (reserved for future use)

    Returns:
        List of LSPToolDefinition instances (empty if no servers configured)

    Example:
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
    # Support both "servers" and "lspServers" keys
    servers = config.get("lspServers") or config.get("servers") or {}

    if not servers:
        logger.debug("No LSP servers configured, skipping LSP tool creation")
        return []

    # Normalize config to always use "servers" key internally
    normalized_config = {"servers": servers}

    try:
        tools = LSPToolDefinition.create(
            lsp_config=normalized_config,
            workspace_root=workspace_root,
        )
        logger.info(
            f"Created LSP tool with {len(servers)} server(s): {list(servers.keys())}"
        )
        return list(tools)
    except Exception as e:
        logger.error(f"Failed to create LSP tools: {e}", exc_info=True)
        return []


def validate_lsp_config(config: dict[str, Any]) -> list[str]:
    """Validate LSP configuration and return list of errors.

    Args:
        config: LSP configuration dictionary

    Returns:
        List of validation error messages (empty if valid)
    """
    errors: list[str] = []
    servers = config.get("lspServers") or config.get("servers") or {}

    if not isinstance(servers, dict):
        errors.append("LSP servers configuration must be a dictionary")
        return errors

    for name, server_config in servers.items():
        if not isinstance(server_config, dict):
            errors.append(f"Server '{name}' configuration must be a dictionary")
            continue

        if "command" not in server_config:
            errors.append(f"Server '{name}' is missing required 'command' field")

        ext_to_lang = server_config.get("extensionToLanguage") or server_config.get(
            "extension_to_language"
        )
        if not ext_to_lang:
            errors.append(
                f"Server '{name}' is missing 'extensionToLanguage' mapping. "
                "Without this, no files will be routed to the server."
            )
        elif not isinstance(ext_to_lang, dict):
            errors.append(
                f"Server '{name}' extensionToLanguage must be a dictionary "
                "mapping file extensions to language IDs"
            )

    return errors
