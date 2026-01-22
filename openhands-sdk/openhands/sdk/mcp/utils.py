"""Utility functions for MCP integration."""

import logging
from collections.abc import Iterator

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
    """List tools from MCP client and keep connection open."""
    await client.connect()
    if not client.is_connected():
        raise RuntimeError("MCP client failed to connect")

    mcp_type_tools: list[mcp.types.Tool] = await client.list_tools()
    tools: list[ToolDefinition] = []
    for mcp_tool in mcp_type_tools:
        tool_sequence = MCPToolDefinition.create(mcp_tool=mcp_tool, mcp_client=client)
        tools.extend(tool_sequence)
    return tools


class MCPToolset:
    """A collection of MCP tools with explicit lifecycle management.

    This class owns the MCP client connection and provides clear ownership
    semantics. Use it as a context manager for automatic cleanup:

        with create_mcp_tools(config) as toolset:
            for tool in toolset.tools:
                # use tool
            # Connection automatically closed on exit

    Or manage lifecycle manually:

        toolset = create_mcp_tools(config)
        try:
            for tool in toolset.tools:
                # use tool
        finally:
            toolset.close()
    """

    def __init__(self, tools: list[MCPToolDefinition], client: MCPClient):
        self._tools = tools
        self._client = client

    @property
    def tools(self) -> list[MCPToolDefinition]:
        """The list of MCP tools."""
        return self._tools

    @property
    def client(self) -> MCPClient:
        """The underlying MCP client (for advanced use cases)."""
        return self._client

    def close(self) -> None:
        """Close the MCP client connection."""
        self._client.sync_close()

    def __enter__(self) -> "MCPToolset":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[MCPToolDefinition]:
        """Allow iterating directly over the toolset."""
        return iter(self._tools)

    def __len__(self) -> int:
        """Return the number of tools."""
        return len(self._tools)

    def __getitem__(self, index: int) -> MCPToolDefinition:
        """Allow indexing into the toolset."""
        return self._tools[index]


def create_mcp_tools(
    config: dict | MCPConfig,
    timeout: float = 30.0,
) -> MCPToolset:
    """Create MCP tools from MCP configuration.

    Returns an MCPToolset that owns the client connection. Use it as a
    context manager for automatic cleanup, or call close() when done:

        # Context manager (recommended):
        with create_mcp_tools(config) as toolset:
            for tool in toolset.tools:
                # use tool

        # Manual cleanup:
        toolset = create_mcp_tools(config)
        try:
            for tool in toolset.tools:
                # use tool
        finally:
            toolset.close()

    Args:
        config: MCP configuration dict or MCPConfig object
        timeout: Timeout for connecting and listing tools (default 30s)

    Returns:
        MCPToolset containing the tools and owning the client connection
    """
    if isinstance(config, dict):
        config = MCPConfig.model_validate(config)
    client = MCPClient(config, log_handler=log_handler)

    try:
        tools = client.call_async_from_sync(
            _list_tools_and_keep_connected, timeout=timeout, client=client
        )
    except TimeoutError as e:
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
        try:
            client.sync_close()
        except Exception as close_exc:
            logger.warning(
                "Failed to close MCP client during error cleanup", exc_info=close_exc
            )
        raise

    logger.info(f"Created {len(tools)} MCP tools: {[t.name for t in tools]}")
    return MCPToolset(tools=tools, client=client)
