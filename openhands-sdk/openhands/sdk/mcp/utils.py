"""Utility functions for MCP integration."""

import logging

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


def _get_effective_timeout(config: MCPConfig, default_timeout: float) -> float:
    """Determine the effective timeout from config, respecting per-server timeouts.

    If any server specifies a timeout, use the maximum of all specified timeouts
    and the default. This ensures slow servers (e.g., OAuth-based) have enough time.
    """
    if not config.mcpServers:
        return default_timeout

    max_server_timeout = default_timeout
    for server_config in config.mcpServers.values():
        server_timeout = getattr(server_config, "timeout", None)
        if server_timeout is not None:
            max_server_timeout = max(max_server_timeout, server_timeout)

    return max_server_timeout


def create_mcp_tools(
    config: dict | MCPConfig,
    timeout: float = 60.0,
) -> MCPClient:
    """Create MCP tools from MCP configuration.

    Args:
        config: MCP configuration dict or MCPConfig object.
        timeout: Default timeout in seconds for MCP connections.
            Individual server timeouts in config take precedence.

    Returns an MCPClient with tools populated. Use as a context manager:

        with create_mcp_tools(config) as client:
            for tool in client.tools:
                # use tool
        # Connection automatically closed
    """
    if isinstance(config, dict):
        config = MCPConfig.model_validate(config)

    effective_timeout = _get_effective_timeout(config, timeout)
    client = MCPClient(config, log_handler=log_handler)

    try:
        client.call_async_from_sync(
            _connect_and_list_tools, timeout=effective_timeout, client=client
        )
    except TimeoutError as e:
        client.sync_close()
        server_names = (
            list(config.mcpServers.keys()) if config.mcpServers else ["unknown"]
        )
        error_msg = (
            f"MCP tool listing timed out after {effective_timeout} seconds.\n"
            f"MCP servers configured: {', '.join(server_names)}\n\n"
            "Possible solutions:\n"
            f"  1. Set mcp_timeout in your agent config (current: {effective_timeout}s)\n"
            "  2. Set timeout per-server in mcp_config\n"
            "  3. Check if the MCP server is running and responding\n"
            "  4. Verify network connectivity to the MCP server\n"
        )
        raise MCPTimeoutError(
            error_msg, timeout=effective_timeout, config=config.model_dump()
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
