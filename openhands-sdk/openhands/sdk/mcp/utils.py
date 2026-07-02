"""Utility functions for MCP integration."""

import logging
from collections.abc import Callable, Sequence

import mcp.types
from fastmcp.client.logging import LogMessage
from fastmcp.client.messages import MessageHandler
from fastmcp.mcp_config import MCPConfig

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.exceptions import MCPTimeoutError
from openhands.sdk.mcp.tool import MCPToolDefinition


logger = get_logger(__name__)
LOGGING_LEVEL_MAP = logging.getLevelNamesMapping()


# Callback invoked when an MCP server signals that its tool list changed.
# Receives the *newly added* tool definitions; removed tools are dropped from
# the owning client's tool list but are not reported here.
ToolsChangedCallback = Callable[[Sequence[MCPToolDefinition]], None]


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


async def _refresh_tools(
    client: MCPClient,
    on_tools_changed: ToolsChangedCallback | None = None,
) -> None:
    """Re-list tools from the server and reconcile ``client._tools``.

    Called both at connect time and whenever the server sends a
    ``notifications/tools/list_changed`` notification. Newly discovered tools
    are appended to ``client._tools`` and, when an ``on_tools_changed``
    callback is supplied, reported to it so a running agent can register them
    via ``add_runtime_tools``. Tools that are no longer advertised by the
    server are dropped from ``client._tools`` (but are not proactively removed
    from an agent's tool map).
    """
    mcp_type_tools: list[mcp.types.Tool] = await client.list_tools()
    existing_by_name = {tool.name: tool for tool in client._tools}
    server_names = {mcp_tool.name for mcp_tool in mcp_type_tools}

    reconciled: list[MCPToolDefinition] = []
    added: list[MCPToolDefinition] = []
    for mcp_tool in mcp_type_tools:
        prior = existing_by_name.get(mcp_tool.name)
        if prior is not None:
            # Preserve the existing definition so its executor (and the
            # shared MCPClient it closes on shutdown) stays wired up.
            reconciled.append(prior)
            continue
        tool_sequence = MCPToolDefinition.create(mcp_tool=mcp_tool, mcp_client=client)
        reconciled.extend(tool_sequence)
        added.extend(tool_sequence)

    # Drop tools the server no longer advertises. Reassign atomically so
    # concurrent readers iterating client.tools never observe mid-update state.
    removed = [
        tool.name for name, tool in existing_by_name.items() if name not in server_names
    ]
    if removed:
        logger.info("MCP server removed tools: %s", ", ".join(sorted(removed)))
    client._tools = reconciled

    if added and on_tools_changed is not None:
        try:
            on_tools_changed(added)
        except Exception:
            logger.warning(
                "on_tools_changed callback failed for %d new MCP tools",
                len(added),
                exc_info=True,
            )


class _ToolListChangedHandler(MessageHandler):
    """Message handler that refreshes tools on ``tools/list_changed``.

    Some MCP servers (e.g. Datadog's hosted server) use progressive
    disclosure: they expose a small gateway toolset at connect time and
    register additional tools only after a skill-loading tool is invoked,
    signalling the change with ``notifications/tools/list_changed``. Without
    subscribing, the client never re-lists and the new tools stay invisible.
    """

    def __init__(
        self,
        client: MCPClient,
        on_tools_changed: ToolsChangedCallback | None = None,
    ):
        super().__init__()
        self._client = client
        self._on_tools_changed = on_tools_changed

    async def on_tool_list_changed(
        self,
        message: mcp.types.ToolListChangedNotification,  # noqa: ARG002
    ) -> None:
        client = self._client
        if client._closed:
            return
        logger.debug("MCP tools/list_changed received; refreshing tools")
        try:
            await _refresh_tools(client, self._on_tools_changed)
        except Exception:
            logger.warning(
                "Failed to refresh MCP tools after list_changed notification",
                exc_info=True,
            )


def create_mcp_tools(
    config: dict | MCPConfig,
    timeout: float = 30.0,
    on_tools_changed: ToolsChangedCallback | None = None,
) -> MCPClient:
    """Create MCP tools from MCP configuration.

    Returns an MCPClient with tools populated. Use as a context manager:

        with create_mcp_tools(config) as client:
            for tool in client.tools:
                # use tool
        # Connection automatically closed

    When ``on_tools_changed`` is provided, the client also subscribes to the
    server's ``notifications/tools/list_changed`` notifications. Each time the
    server advertises a changed tool list, the client re-lists tools and
    invokes the callback with the *newly added* tool definitions. This enables
    progressive-disclosure MCP servers (which register tools dynamically after
    a skill-loading call) to surface their full toolset to the agent. The
    callback runs on the client's background event-loop thread, so callers
    must ensure it is thread-safe (e.g. ``Agent.add_runtime_tools``).
    """
    if isinstance(config, dict):
        config = MCPConfig.model_validate(config)
    handler = _ToolListChangedHandler(
        client=None,  # type: ignore[arg-type]
        on_tools_changed=on_tools_changed,
    )
    client = MCPClient(config, log_handler=log_handler, message_handler=handler)
    handler._client = client

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

    logger.info("Created %d MCP tools", len(client.tools))
    return client
