"""Utility functions for MCP integration."""

import logging
from collections.abc import Mapping
from typing import Any

import mcp.types
from fastmcp.client.auth import OAuth
from fastmcp.client.logging import LogMessage
from fastmcp.mcp_config import MCPConfig as FastMCPConfig, RemoteMCPServer
from key_value.aio.protocols import AsyncKeyValue

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPConfig, MCPServer, to_fastmcp_mcp_config
from openhands.sdk.mcp.exceptions import MCPTimeoutError
from openhands.sdk.mcp.tool import MCPToolDefinition


logger = get_logger(__name__)
LOGGING_LEVEL_MAP = logging.getLevelNamesMapping()


def _oauth_auth_from_authentication_config(
    authentication: dict[str, Any] | None,
    *,
    mcp_oauth_token_storage: AsyncKeyValue | None = None,
) -> OAuth | None:
    """Build FastMCP OAuth auth from explicit SDK MCP auth metadata."""
    if not authentication or authentication.get("type") != "oauth":
        return None

    additional_client_metadata = dict(
        authentication.get("additional_client_metadata") or {}
    )
    client_auth_method = authentication.get("client_auth_method")
    if client_auth_method is not None:
        if client_auth_method not in {
            "none",
            "client_secret_post",
            "client_secret_basic",
            "private_key_jwt",
        }:
            raise ValueError(
                "MCP OAuth authentication.client_auth_method must be one of "
                "'none', 'client_secret_post', 'client_secret_basic', or "
                "'private_key_jwt'"
            )
        additional_client_metadata["token_endpoint_auth_method"] = client_auth_method

    kwargs: dict[str, Any] = {}
    if additional_client_metadata:
        kwargs["additional_client_metadata"] = additional_client_metadata
    for source_key, target_key in (
        ("scopes", "scopes"),
        ("client_name", "client_name"),
        ("client_metadata_url", "client_metadata_url"),
    ):
        value = authentication.get(source_key)
        if value is not None:
            kwargs[target_key] = value
    if mcp_oauth_token_storage is not None:
        kwargs["token_storage"] = mcp_oauth_token_storage

    return OAuth(**kwargs)


def _prepare_mcp_config(
    mcp_config: MCPConfig,
    *,
    mcp_oauth_token_storage: AsyncKeyValue | None = None,
) -> FastMCPConfig:
    """Validate MCP config and apply explicit OpenHands runtime auth metadata."""
    prepared = FastMCPConfig.model_validate(to_fastmcp_mcp_config(mcp_config))

    for server in prepared.mcpServers.values():
        if not isinstance(server, RemoteMCPServer) or server.auth != "oauth":
            continue
        oauth_auth = _oauth_auth_from_authentication_config(
            server.authentication,
            mcp_oauth_token_storage=mcp_oauth_token_storage,
        )
        if oauth_auth is not None:
            server.auth = oauth_auth
        elif mcp_oauth_token_storage is not None:
            server.auth = OAuth(token_storage=mcp_oauth_token_storage)

    return prepared


def _require_native_mcp_config(mcp_config: Mapping[str, MCPServer]) -> MCPConfig:
    if not isinstance(mcp_config, Mapping):
        raise TypeError(
            "create_mcp_tools expects native MCP servers: dict[str, MCPServer]. "
            "Use coerce_mcp_config() at external config boundaries."
        )

    invalid = [
        name
        for name, server in mcp_config.items()
        if not isinstance(name, str) or not isinstance(server, MCPServer)
    ]
    if invalid:
        raise TypeError(
            "create_mcp_tools expects native MCP servers: dict[str, MCPServer]. "
            "Use coerce_mcp_config() at external config boundaries."
        )
    return dict(mcp_config)


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
    mcp_config: MCPConfig,
    timeout: float = 30.0,
    *,
    mcp_oauth_token_storage: AsyncKeyValue | None = None,
) -> MCPClient:
    """Create MCP tools from OpenHands-native MCP server settings.

    Returns an MCPClient with tools populated. Use as a context manager:

        with create_mcp_tools(mcp_config) as client:
            for tool in client.tools:
                # use tool
        # Connection automatically closed
    """
    mcp_config = _require_native_mcp_config(mcp_config)
    config = _prepare_mcp_config(
        mcp_config,
        mcp_oauth_token_storage=mcp_oauth_token_storage,
    )
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

    logger.info("Created %d MCP tools", len(client.tools))
    return client
