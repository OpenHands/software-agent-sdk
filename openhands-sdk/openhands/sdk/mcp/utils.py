"""Utility functions for MCP integration."""

import logging

import mcp.types
from fastmcp.client.logging import LogMessage
from fastmcp.mcp_config import MCPConfig

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.exceptions import MCPTimeoutError
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.tool.tool import ToolDefinition


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


async def _list_tools_and_keep_connected(client: MCPClient) -> list[ToolDefinition]:
    """List tools from MCP client and keep connection open.

    Unlike the old approach that closed the connection after listing,
    this keeps the connection open so subsequent tool calls can reuse it.
    The connection should be closed via client.sync_close() when done.
    """
    tools: list[ToolDefinition] = []

    # Establish connection (will be closed via sync_close() when done)
    await client.connect()

    if not client.is_connected():
        raise RuntimeError("MCP client failed to connect")

    mcp_type_tools: list[mcp.types.Tool] = await client.list_tools()
    for mcp_tool in mcp_type_tools:
        tool_sequence = MCPToolDefinition.create(mcp_tool=mcp_tool, mcp_client=client)
        tools.extend(tool_sequence)

    # Connection stays open for subsequent tool calls.
    # It will be closed when client.sync_close() is called.
    return tools


def create_mcp_tools(
    config: dict | MCPConfig,
    timeout: float = 30.0,
) -> list[MCPToolDefinition]:
    """Create MCP tools from MCP configuration.

    The returned tools share a persistent MCP client connection.
    Call close() on any tool's executor when done to clean up.
    """
    tools: list[MCPToolDefinition] = []
    if isinstance(config, dict):
        config = MCPConfig.model_validate(config)
    client = MCPClient(config, log_handler=log_handler)

    try:
        tools = client.call_async_from_sync(
            _list_tools_and_keep_connected, timeout=timeout, client=client
        )
    except TimeoutError as e:
        # Clean up on timeout
        client.sync_close()
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
    except Exception:
        # Clean up on any error (don't mask the original exception)
        try:
            client.sync_close()
        except Exception:
            pass
        raise

    logger.info(f"Created {len(tools)} MCP tools: {[t.name for t in tools]}")
    return tools
