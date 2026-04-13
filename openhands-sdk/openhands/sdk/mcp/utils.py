"""Utility functions for MCP integration."""

from __future__ import annotations

import logging
from typing import Any

import mcp.types
from fastmcp.client.logging import LogMessage
from fastmcp.mcp_config import MCPConfig

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.exceptions import MCPTimeoutError
from openhands.sdk.mcp.tool import MCPToolDefinition


logger = get_logger(__name__)
LOGGING_LEVEL_MAP = logging.getLevelNamesMapping()


async def log_handler(message: LogMessage):
    """
    Handles incoming logs from the MCP server and forwards them
    to the standard Python logging system.
    """
    msg = message.data.get("msg")
    extra = message.data.get("extra")

    # Convert the MCP log level to a Python log level
    level = LOGGING_LEVEL_MAP.get(message.level.upper(), logging.INFO)

    # Log the message using the standard logging library
    logger.log(level, msg, extra=extra)


async def _connect_and_list_tools(client: MCPClient) -> None:
    """Connect to MCP server and populate client._tools."""
    await client.connect()
    mcp_type_tools: list[mcp.types.Tool] = await client.list_tools()
    for mcp_tool in mcp_type_tools:
        tool_sequence = MCPToolDefinition.create(mcp_tool=mcp_tool, mcp_client=client)
        client._tools.extend(tool_sequence)


def create_mcp_tools(
    config: dict | MCPConfig,
    timeout: float = 30.0,
) -> MCPClient:
    """Create MCP tools from MCP configuration.

    Returns an MCPClient with tools populated. Use as a context manager:

        with create_mcp_tools(config) as client:
            for tool in client.tools:
                # use tool
        # Connection automatically closed
    """
    if isinstance(config, dict):
        config = MCPConfig.model_validate(config)
    client = MCPClient(config, log_handler=log_handler)

    try:
        client.call_async_from_sync(
            _connect_and_list_tools, timeout=timeout, client=client
        )
    except TimeoutError as e:
        client.sync_close()
        # Extract server names from config for better error message
        server_names = (
            list(config.mcpServers.keys()) if config.mcpServers else ["unknown"]
        )
        error_msg = (
            f"MCP tool listing timed out after {timeout} seconds.\n"
            f"MCP servers configured: {', '.join(server_names)}\n\n"
            "Possible solutions:\n"
            "  1. Increase the timeout value (default is 30 seconds)\n"
            "  2. Check if the MCP server is running and responding\n"
            "  3. Verify network connectivity to the MCP server\n"
        )
        raise MCPTimeoutError(
            error_msg, timeout=timeout, config=config.model_dump()
        ) from e
    except BaseException:
        try:
            client.sync_close()
        except Exception as close_exc:
            logger.warning(
                "Failed to close MCP client during error cleanup", exc_info=close_exc
            )
        raise

    logger.info(f"Created {len(client.tools)} MCP tools: {[t.name for t in client]}")
    return client


def merge_mcp_configs(
    base: dict[str, Any] | None,
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge two MCP configuration dicts with last-wins semantics.

    Merge rules (Claude Code compatible):
    - ``mcpServers``: deep-merge by server name (override wins per server)
    - Other top-level keys: shallow override (override wins)

    Neither input dict is mutated.

    Args:
        base: Existing MCP config (may be ``None`` or empty).
        override: MCP config to layer on top (may be ``None`` or empty).

    Returns:
        A new merged dict.  Empty dict if both inputs are ``None``.
    """
    if base is None and override is None:
        return {}
    if base is None:
        return dict(override) if override else {}
    if override is None:
        return dict(base)

    result = dict(base)

    # Merge mcpServers by server name
    if "mcpServers" in override:
        existing_servers = result.get("mcpServers", {})
        for server_name in override["mcpServers"]:
            if server_name in existing_servers:
                logger.warning("MCP server '%s' overridden during merge", server_name)
        result["mcpServers"] = {
            **existing_servers,
            **override["mcpServers"],
        }

    # Other top-level keys: override wins
    for key, value in override.items():
        if key != "mcpServers":
            if key in result:
                logger.warning("MCP config key '%s' overridden during merge", key)
            result[key] = value

    return result
