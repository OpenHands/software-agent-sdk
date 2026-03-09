"""Utility functions for MCP integration."""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import mcp.types
from fastmcp.client.logging import LogMessage
from fastmcp.mcp_config import MCPConfig

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.exceptions import MCPServerError, MCPTimeoutError
from openhands.sdk.mcp.tool import MCPToolDefinition


if TYPE_CHECKING:
    from fastmcp.mcp_config import MCPServerTypes

logger = get_logger(__name__)
LOGGING_LEVEL_MAP = logging.getLevelNamesMapping()


@dataclass
class MCPToolsResult:
    """Result of creating MCP tools with graceful degradation.

    Contains both the successfully initialized tools and any errors that occurred
    during initialization of individual servers.
    """

    tools: list[MCPToolDefinition] = field(default_factory=list)
    errors: list[MCPServerError] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """Return True if any server failed to initialize."""
        return len(self.errors) > 0

    def error_summary(self) -> str:
        """Return a human-readable summary of all errors."""
        if not self.errors:
            return ""
        parts = [f"- {err.server_name}: {err}" for err in self.errors]
        return "MCP server initialization failures:\n" + "\n".join(parts)


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


def _create_single_server_tools(
    server_name: str,
    server_config: "MCPServerTypes",
    timeout: float,
) -> MCPClient:
    """Create MCP tools for a single server.

    Internal helper used by create_mcp_tools_graceful.
    Raises exceptions on failure (caller handles graceful degradation).
    """
    single_server_config = MCPConfig(mcpServers={server_name: server_config})
    client = MCPClient(single_server_config, log_handler=log_handler)

    try:
        client.call_async_from_sync(
            _connect_and_list_tools, timeout=timeout, client=client
        )
    except TimeoutError as e:
        client.sync_close()
        raise MCPTimeoutError(
            f"MCP server '{server_name}' timed out after {timeout} seconds",
            timeout=timeout,
            config=single_server_config.model_dump(),
        ) from e
    except BaseException:
        try:
            client.sync_close()
        except Exception as close_exc:
            logger.warning(
                f"Failed to close MCP client for '{server_name}' during error cleanup",
                exc_info=close_exc,
            )
        raise

    return client


def create_mcp_tools_graceful(
    config: dict | MCPConfig,
    timeout: float = 30.0,
) -> MCPToolsResult:
    """Create MCP tools with per-server graceful degradation.

    Unlike create_mcp_tools() which fails if ANY server fails, this function
    attempts to initialize each MCP server individually and continues even
    if some servers fail.

    Args:
        config: MCP configuration dictionary or MCPConfig object containing
            server definitions.
        timeout: Timeout in seconds for each server connection (default: 30.0).

    Returns:
        MCPToolsResult containing:
        - tools: List of successfully loaded MCPToolDefinitions from all servers
        - errors: List of MCPServerError for any servers that failed

    Example:
        result = create_mcp_tools_graceful(config)
        if result.has_errors:
            logger.warning(result.error_summary())
        for tool in result.tools:
            # use tool
    """
    if isinstance(config, dict):
        config = MCPConfig.model_validate(config)

    if not config.mcpServers:
        return MCPToolsResult()

    result = MCPToolsResult()

    for server_name, server_config in config.mcpServers.items():
        try:
            client = _create_single_server_tools(server_name, server_config, timeout)
            result.tools.extend(client.tools)
            logger.info(
                f"MCP server '{server_name}' connected: {len(client.tools)} tools"
            )
        except Exception as e:
            error = MCPServerError(
                message=str(e),
                server_name=server_name,
                cause=e,
            )
            result.errors.append(error)
            logger.warning(f"MCP server '{server_name}' failed to initialize: {e}")

    if result.tools:
        logger.info(
            f"Created {len(result.tools)} MCP tools from "
            f"{len(config.mcpServers) - len(result.errors)} servers"
        )
    if result.errors:
        logger.warning(
            f"{len(result.errors)} MCP server(s) failed: "
            f"{[e.server_name for e in result.errors]}"
        )

    return result
