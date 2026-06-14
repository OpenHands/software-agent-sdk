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


def _extract_timeout_from_config(config: MCPConfig) -> float | None:
    """Extract the tool execution timeout from MCP server configurations.

    Reads per-server ``timeout`` fields (in milliseconds) from the MCP config
    and converts to seconds.  When multiple servers are configured, returns
    the maximum timeout so that slower servers are still reachable.

    Returns None if no server specifies a timeout.
    """
    if not config.mcpServers:
        return None

    timeouts_sec: list[float] = []
    for server_name, server in config.mcpServers.items():
        server_timeout = getattr(server, "timeout", None)
        if server_timeout is not None:
            # FastMCP stores timeout in milliseconds; convert to seconds
            timeouts_sec.append(server_timeout / 1000.0)
            logger.debug(
                "Server '%s' configured with timeout: %d ms (%.1f s)",
                server_name,
                server_timeout,
                server_timeout / 1000.0,
            )

    if not timeouts_sec:
        return None

    # Use the maximum timeout across all servers to ensure all are reachable
    return max(timeouts_sec)


async def _connect_and_list_tools(
    client: MCPClient,
    tool_timeout: float | None = None,
) -> None:
    """Connect to MCP server and populate client._tools."""
    await client.connect()
    mcp_type_tools: list[mcp.types.Tool] = await client.list_tools()
    for mcp_tool in mcp_type_tools:
        tool_sequence = MCPToolDefinition.create(
            mcp_tool=mcp_tool,
            mcp_client=client,
            timeout=tool_timeout,
        )
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

    # Extract per-server tool timeout from config (milliseconds → seconds).
    # Falls back to MCP_TOOL_TIMEOUT_SECONDS inside MCPToolDefinition.create()
    # when no server specifies a timeout.
    tool_timeout = _extract_timeout_from_config(config)

    try:
        client.call_async_from_sync(
            _connect_and_list_tools,
            timeout=timeout,
            client=client,
            tool_timeout=tool_timeout,
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

    logger.info("Created %d MCP tools", len(client.tools))
    return client
