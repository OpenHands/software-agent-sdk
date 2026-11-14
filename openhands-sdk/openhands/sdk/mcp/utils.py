"""Utility functions for MCP integration."""

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

import httpx2
import mcp.types
from fastmcp.client.auth import OAuth
from fastmcp.client.logging import LogMessage
from fastmcp.client.messages import MessageHandler
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.mcp_config import MCPConfig as FastMCPConfig, RemoteMCPServer
from key_value.aio.protocols import AsyncKeyValue
from mcp.client.auth.utils import extract_field_from_www_auth
from mcp.shared._httpx_utils import create_mcp_http_client
from pydantic import PrivateAttr, ValidationError

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient, ToolsReconciledCallback
from openhands.sdk.mcp.config import (
    MCPOAuthAuthCredential,
    MCPOAuthAuthentication,
    MCPServer,
    enabled_mcp_servers,
    to_fastmcp_mcp_config,
)
from openhands.sdk.mcp.exceptions import MCPServerHTTPError, MCPTimeoutError
from openhands.sdk.mcp.oauth import MCPOAuth
from openhands.sdk.mcp.tool import MCPToolDefinition


logger = get_logger(__name__)
LOGGING_LEVEL_MAP = logging.getLevelNamesMapping()

MCPOAuthFactory = Callable[
    [str, MCPServer, MCPOAuthAuthCredential, AsyncKeyValue | None],
    OAuth | None,
]

# Backward-compatible callback that reports only newly added tools.
ToolsChangedCallback = Callable[[Sequence[MCPToolDefinition]], None]


class MCPToolProvider(Protocol):
    """Runtime-only MCP tool materializer."""

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: ToolsChangedCallback | None = None,
        on_tools_reconciled: ToolsReconciledCallback | None = None,
    ) -> MCPClient: ...


class DefaultMCPToolProvider:
    """Runtime MCP tool materializer without extra persistence hooks."""

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: ToolsChangedCallback | None = None,
        on_tools_reconciled: ToolsReconciledCallback | None = None,
    ) -> MCPClient:
        return create_mcp_tools(
            mcp_config,
            timeout,
            on_tools_changed=on_tools_changed,
            on_tools_reconciled=on_tools_reconciled,
        )


def provider_supports_on_tools_reconciled(provider: MCPToolProvider) -> bool:
    """Whether ``provider.create_tools`` accepts ``on_tools_reconciled``.

    Custom ``MCPToolProvider`` implementations written before this parameter
    existed only accept ``on_tools_changed``; passing the new keyword to
    them would raise ``TypeError``. Callers should check this first and omit
    the keyword for providers that don't support it.
    """
    try:
        params = inspect.signature(provider.create_tools).parameters
    except (TypeError, ValueError):
        return False
    return "on_tools_reconciled" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def _oauth_auth_from_authentication_config(
    authentication: MCPOAuthAuthentication | None,
    *,
    mcp_oauth_token_storage: AsyncKeyValue | None = None,
    mcp_oauth_class: type[MCPOAuth] = MCPOAuth,
) -> OAuth | None:
    """Build FastMCP OAuth auth from explicit SDK MCP auth metadata."""
    if authentication is None:
        return None

    additional_client_metadata = dict(authentication.additional_client_metadata or {})
    client_auth_method = authentication.client_auth_method
    if client_auth_method is not None:
        additional_client_metadata["token_endpoint_auth_method"] = client_auth_method

    return mcp_oauth_class(
        scopes=authentication.scopes,
        client_name=authentication.client_name or "FastMCP Client",
        token_storage=mcp_oauth_token_storage,
        additional_client_metadata=additional_client_metadata or None,
        client_metadata_url=authentication.client_metadata_url,
        client_id=authentication.client_id,
        client_secret=authentication.client_secret.get_secret_value()
        if authentication.client_secret is not None
        else None,
    )


class _HTTPErrorLog:
    """The last HTTP error response an MCP server's client received."""

    def __init__(self) -> None:
        self.last: httpx2.Response | None = None

    async def record(self, response: httpx2.Response) -> None:
        if not response.is_error:
            return
        # A streamable-HTTP server without a server-to-client stream answers
        # the client's GET with 405; the spec makes that a normal reply.
        if response.status_code == 405 and response.request.method == "GET":
            return
        # The transport streams responses and may never read an error body.
        await response.aread()
        self.last = response


class _RemoteMCPServer(RemoteMCPServer):
    """A remote server whose HTTP client reports the server's error responses.

    The MCP client turns an HTTP error from the server into a stand-in such as
    ``Server returned an error response`` or ``Session terminated`` and drops
    the response, so the server's reason (e.g. GitLab's "MCP server not
    enabled for any of your groups") never reaches the user. This server's
    client records its error responses in ``_http_errors`` instead.

    A package-declared server also keeps its headers on its own origin (Agent
    Plugins §7.2.1): httpx strips only ``Authorization`` on a cross-origin
    redirect, so every configured header name is dropped from any request
    that leaves it.
    """

    _http_errors: _HTTPErrorLog = PrivateAttr(default_factory=_HTTPErrorLog)
    _bind_headers_to_origin: bool = PrivateAttr(default=False)

    def to_transport(self):  # type: ignore[override]
        transport = super().to_transport()
        if not isinstance(transport, (StreamableHttpTransport, SSETransport)):
            return transport
        request_hooks = []
        if self._bind_headers_to_origin and self.headers:
            request_hooks.append(
                _drop_headers_off_origin(self.url, tuple(self.headers))
            )
        transport.httpx_client_factory = _mcp_http_client_factory(
            request_hooks, self._http_errors.record
        )
        return transport


def _drop_headers_off_origin(url: str, header_names: tuple[str, ...]):
    origin = _origin(httpx2.URL(url))

    async def hook(request: httpx2.Request) -> None:
        # Request hooks run on every redirect hop, after httpx has copied the
        # previous request's headers onto the next one.
        if _origin(request.url) != origin:
            for name in header_names:
                request.headers.pop(name, None)

    return hook


def _mcp_http_client_factory(
    request_hooks: list[Callable[[httpx2.Request], Awaitable[None]]],
    response_hook: Callable[[httpx2.Response], Awaitable[None]],
):
    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **_: object,
    ) -> httpx2.AsyncClient:
        client = create_mcp_http_client(headers=headers, timeout=timeout, auth=auth)
        client.event_hooks = {"request": request_hooks, "response": [response_hook]}
        return client

    return factory


# What the MCP client reports instead of a streamable-HTTP error response.
_HTTP_ERROR_STAND_INS = frozenset(
    {"Server returned an error response", "Session terminated", "Not Found"}
)
_HTTP_ERROR_REASON_MAX_CHARS = 300


def _server_http_error(
    exc: BaseException, config: FastMCPConfig
) -> MCPServerHTTPError | None:
    """The HTTP error response behind ``exc``, if a server rejected a request."""
    if isinstance(exc, httpx2.HTTPStatusError):
        response = exc.response
    elif isinstance(exc, mcp.MCPError) and exc.message in _HTTP_ERROR_STAND_INS:
        responses = [
            server._http_errors.last
            for server in config.mcpServers.values()
            if isinstance(server, _RemoteMCPServer)
        ]
        response = next((r for r in responses if r is not None), None)
    else:
        return None
    if response is None:
        return None
    url = response.request.url
    return MCPServerHTTPError(
        status_code=response.status_code,
        reason_phrase=response.reason_phrase,
        url=f"{url.scheme}://{url.netloc.decode()}",
        reason=_http_error_reason(response),
    )


def _http_error_reason(response: httpx2.Response) -> str | None:
    """The most readable reason a server gave for rejecting a request.

    RFC 6750 puts it in ``WWW-Authenticate``'s ``error_description``, but most
    servers send only an error code there and explain in a JSON body.
    """
    try:
        text = response.text.strip()
    except httpx2.ResponseNotRead:
        text = ""
    candidates = [
        extract_field_from_www_auth(response, "error_description"),
        *_json_body_reasons(text),
        extract_field_from_www_auth(response, "error"),
        text,
    ]
    reason = next((c for c in candidates if c), None)
    if reason is None:
        return None
    reason = " ".join(reason.split())
    if len(reason) > _HTTP_ERROR_REASON_MAX_CHARS:
        reason = reason[: _HTTP_ERROR_REASON_MAX_CHARS - 3] + "..."
    return reason


def _json_body_reasons(text: str) -> list[str]:
    try:
        body = json.loads(text)
    except ValueError:
        return []
    if not isinstance(body, dict):
        return []
    error = body.get("error")
    candidates = [
        body.get("error_description"),
        body.get("message"),
        error.get("message") if isinstance(error, dict) else None,
        body.get("detail"),
        error,
    ]
    return [c for c in candidates if isinstance(c, str)]


def _origin(url: httpx2.URL) -> tuple[str, str, int | None]:
    default_port = {"http": 80, "https": 443}.get(url.scheme)
    return url.scheme, url.host, url.port or default_port


def _prepare_mcp_config(
    mcp_config: dict[str, MCPServer],
    *,
    mcp_oauth_token_storage: AsyncKeyValue | None = None,
    mcp_oauth_factory: MCPOAuthFactory | None = None,
    mcp_oauth_class: type[MCPOAuth] = MCPOAuth,
) -> FastMCPConfig:
    """Validate MCP config and apply explicit OpenHands runtime auth metadata.

    Servers are validated one by one: a server FastMCP rejects is skipped with
    a warning naming it, so it cannot cost its siblings their tools.
    """
    servers = to_fastmcp_mcp_config(mcp_config)["mcpServers"]
    valid: dict[str, Any] = {}
    for server_name, server in servers.items():
        try:
            FastMCPConfig.model_validate({"mcpServers": {server_name: server}})
        except ValidationError as exc:
            logger.warning(
                "Skipping invalid MCP server %s; continuing without its tools: %s",
                server_name,
                exc,
            )
            continue
        valid[server_name] = server
    if servers and not valid:
        raise ValueError(f"No valid MCP servers among: {', '.join(sorted(servers))}")
    prepared = FastMCPConfig.model_validate({"mcpServers": valid})

    for server_name, server in list(prepared.mcpServers.items()):
        if not isinstance(server, RemoteMCPServer):
            continue
        remote = _RemoteMCPServer.model_validate(server.model_dump())
        spec = mcp_config.get(server_name)
        remote._bind_headers_to_origin = spec is not None and spec.literal_values
        prepared.mcpServers[server_name] = remote

    for server_name, server_spec in mcp_config.items():
        auth = server_spec.auth
        if not isinstance(auth, MCPOAuthAuthCredential):
            continue
        server = prepared.mcpServers.get(server_name)
        if not isinstance(server, RemoteMCPServer) or server.auth != "oauth":
            continue
        oauth_auth = (
            mcp_oauth_factory(
                server_name,
                server_spec,
                auth,
                mcp_oauth_token_storage,
            )
            if mcp_oauth_factory is not None
            else _oauth_auth_from_authentication_config(
                auth.authentication,
                mcp_oauth_token_storage=mcp_oauth_token_storage,
                mcp_oauth_class=mcp_oauth_class,
            )
        )
        if oauth_auth is not None:
            server.auth = oauth_auth
        elif mcp_oauth_token_storage is not None:
            server.auth = mcp_oauth_class(token_storage=mcp_oauth_token_storage)

    return prepared


def _require_native_mcp_config(
    mcp_config: Mapping[str, MCPServer],
) -> dict[str, MCPServer]:
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
    await _refresh_tools(client)


async def _refresh_tools(
    client: MCPClient,
    on_tools_changed: ToolsChangedCallback | None = None,
    on_tools_reconciled: ToolsReconciledCallback | None = None,
) -> None:
    """Re-list tools from the server and reconcile ``client._tools``.

    Called after the initial connection and whenever the server sends a
    ``notifications/tools/list_changed`` notification. When an
    ``on_tools_changed`` preserves the original additions-only callback contract.
    ``on_tools_reconciled`` receives the complete current snapshot so a running
    agent can add, replace, and remove tools owned by this client.
    """
    mcp_type_tools: list[mcp.types.Tool] = await client.list_tools()
    existing_by_name = {tool.name: tool for tool in client._tools}
    server_names = {mcp_tool.name for mcp_tool in mcp_type_tools}

    reconciled: list[MCPToolDefinition] = []
    added: list[MCPToolDefinition] = []
    updated: list[MCPToolDefinition] = []
    for mcp_tool in mcp_type_tools:
        prior = existing_by_name.get(mcp_tool.name)
        if prior is not None and prior.mcp_tool == mcp_tool:
            reconciled.append(prior)
            continue
        tool_sequence = MCPToolDefinition.create(mcp_tool=mcp_tool, mcp_client=client)
        reconciled.extend(tool_sequence)
        if prior is None:
            added.extend(tool_sequence)
        else:
            updated.extend(tool_sequence)

    # Drop tools the server no longer advertises. Reassign atomically so
    # concurrent readers iterating client.tools never observe mid-update state.
    removed = [
        tool.name for name, tool in existing_by_name.items() if name not in server_names
    ]
    if removed:
        logger.info("MCP server removed tools: %s", ", ".join(sorted(removed)))
    if updated:
        logger.info(
            "MCP server updated tools: %s",
            ", ".join(sorted(tool.name for tool in updated)),
        )
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

    if (added or updated or removed) and on_tools_reconciled is not None:
        try:
            on_tools_reconciled(client, reconciled)
        except Exception:
            logger.warning(
                "on_tools_reconciled callback failed for MCP tool refresh",
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
        self._refresh_lock = asyncio.Lock()
        self._refresh_tasks: set[asyncio.Task[None]] = set()

    async def on_tool_list_changed(
        self,
        message: mcp.types.ToolListChangedNotification,  # noqa: ARG002
    ) -> None:
        client = self._client
        if client._closed:
            return
        logger.debug("MCP tools/list_changed received; refreshing tools")
        # Keep the receive loop free to process the list_tools response.
        task = asyncio.create_task(self._refresh_tools())
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    async def _refresh_tools(self) -> None:
        client = self._client
        try:
            async with self._refresh_lock:
                if client._closed:
                    return
                await _refresh_tools(
                    client,
                    self._on_tools_changed,
                    client._tools_reconciled_callback,
                )
        except Exception:
            logger.warning(
                "Failed to refresh MCP tools after list_changed notification",
                exc_info=True,
            )


def create_mcp_tools(
    mcp_config: dict[str, MCPServer],
    timeout: float = 30.0,
    *,
    on_tools_changed: ToolsChangedCallback | None = None,
    on_tools_reconciled: ToolsReconciledCallback | None = None,
    mcp_oauth_token_storage: AsyncKeyValue | None = None,
    mcp_oauth_factory: MCPOAuthFactory | None = None,
    mcp_oauth_class: type[MCPOAuth] = MCPOAuth,
) -> MCPClient:
    """Create MCP tools from OpenHands-native MCP server settings.

    Returns an MCPClient with tools populated. Use as a context manager:

        with create_mcp_tools(mcp_config) as client:
            for tool in client.tools:
                # use tool
        # Connection automatically closed

    The client subscribes to ``notifications/tools/list_changed`` and
    reconciles its tool list whenever the server signals a change. When
    ``on_tools_changed`` is provided, the client invokes it with newly added
    tool definitions, preserving the original callback contract. When
    ``on_tools_reconciled`` is provided, it receives the client and complete
    current tool snapshot after additions, updates, or removals. Callbacks run
    on the client's background event-loop thread and must be thread-safe.
    """
    mcp_config = _require_native_mcp_config(mcp_config)
    requested = mcp_config
    mcp_config = enabled_mcp_servers(mcp_config)
    if requested and not mcp_config:
        raise ValueError(
            "All configured MCP servers are disabled: "
            f"{', '.join(sorted(requested))}. Enable at least one, or skip "
            "the call entirely."
        )
    config = _prepare_mcp_config(
        mcp_config,
        mcp_oauth_token_storage=mcp_oauth_token_storage,
        mcp_oauth_factory=mcp_oauth_factory,
        mcp_oauth_class=mcp_oauth_class,
    )
    handler = _ToolListChangedHandler(
        client=None,  # type: ignore[arg-type]
        on_tools_changed=on_tools_changed,
    )
    client = MCPClient(config, log_handler=log_handler, message_handler=handler)
    handler._client = client
    client._tools_reconciled_callback = on_tools_reconciled

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
    except BaseException as exc:
        try:
            client.sync_close()
        except Exception as close_exc:
            logger.warning(
                "Failed to close MCP client during error cleanup", exc_info=close_exc
            )
        http_error = _server_http_error(exc, config)
        if http_error is not None:
            raise http_error from exc
        raise

    logger.info("Created %d MCP tools", len(client.tools))
    return client
