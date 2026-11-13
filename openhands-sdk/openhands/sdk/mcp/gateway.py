"""Versioned, conversation-local MCP catalog and downstream session owner."""

import re
import threading
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer, enabled_mcp_servers
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.mcp.tool_policy import MCPToolPolicy
from openhands.sdk.mcp.utils import (
    MCPToolProvider,
    provider_supports_on_tools_reconciled,
)
from openhands.sdk.tool.builtins.mcp_gateway import MCPGatewayObservation


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


logger = get_logger(__name__)
_GATEWAY_STARTUP_TIMEOUT = 30.0


class MCPToolGateway:
    """Own downstream sessions; expose selected tools without registering them."""

    def __init__(
        self,
        selected_tools: Sequence[str] | None = None,
        *,
        filter_tools_regex: str | None = None,
        tool_policy: Mapping[str, MCPToolPolicy] | None = None,
    ):
        self._lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._active_calls = 0
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, MCPToolDefinition] = {}
        self._selected = (
            frozenset(selected_tools) if selected_tools is not None else None
        )
        self._filter = re.compile(filter_tools_regex) if filter_tools_regex else None
        self._tool_policy = {
            server: rule.model_copy(deep=True)
            for server, rule in (tool_policy or {}).items()
        }
        self._epoch = uuid.uuid4().hex
        self._version = 0
        self._closed = False

    @property
    def clients(self) -> Mapping[str, MCPClient]:
        with self._lock:
            return dict(self._clients)

    @property
    def revision(self) -> str:
        with self._lock:
            return f"{self._epoch}:{self._version}"

    def connect(
        self, config: Mapping[str, MCPServer], provider: MCPToolProvider
    ) -> None:
        """Connect enabled servers in parallel and take their initial catalogs."""
        if not provider_supports_on_tools_reconciled(provider):
            raise ValueError("MCP gateway requires tool-list reconciliation support")
        enabled = enabled_mcp_servers(dict(config))
        if not enabled:
            return
        with ThreadPoolExecutor(
            max_workers=len(enabled), thread_name_prefix="mcp-gateway"
        ) as pool:
            started = {
                name: pool.submit(
                    provider.create_tools,
                    {name: server},
                    _GATEWAY_STARTUP_TIMEOUT,
                    on_tools_reconciled=lambda client, tools, name=name: (
                        self._reconcile(name, client, tools)
                    ),
                )
                for name, server in enabled.items()
            }
        try:
            for name, future in started.items():
                try:
                    client = future.result()
                except Exception:
                    logger.warning("MCP gateway could not connect to %s", name)
                    continue
                with self._lock:
                    self._clients[name] = client
                    self._tools.update(
                        {f"{name}.{tool.name}": tool for tool in client.tools}
                    )
        except BaseException:
            self.close()
            raise

    def add_server(
        self, name: str, server: MCPServer, provider: MCPToolProvider
    ) -> None:
        """Connect one new server without touching existing sessions."""
        if not provider_supports_on_tools_reconciled(provider):
            raise ValueError("MCP gateway requires tool-list reconciliation support")
        with self._lock:
            if self._closed or self._active_calls:
                raise RuntimeError("MCP gateway is unavailable for new connections")
            if name in self._clients:
                raise ValueError(f"MCP server already exists: {name}")

        client = provider.create_tools(
            {name: server},
            _GATEWAY_STARTUP_TIMEOUT,
            on_tools_reconciled=lambda client, tools: self._reconcile(
                name, client, tools
            ),
        )
        try:
            with self._lock:
                if self._closed or self._active_calls or name in self._clients:
                    raise RuntimeError("MCP gateway changed during server addition")
                tools = {f"{name}.{tool.name}": tool for tool in client.tools}
                self._clients[name] = client
                self._tools.update(tools)
                self._version += 1
        except BaseException:
            client.sync_close()
            raise

    def _reconcile(
        self, name: str, client: MCPClient, tools: Sequence[MCPToolDefinition]
    ) -> None:
        with self._lock:
            if self._closed or self._clients.get(name) is not client:
                return
            updated = {
                **{
                    key: value
                    for key, value in self._tools.items()
                    if not key.startswith(f"{name}.")
                },
                **{f"{name}.{tool.name}": tool for tool in tools},
            }
            if updated != self._tools:
                self._tools = updated
                self._version += 1

    def select_tools(self, names: Sequence[str] | None) -> None:
        selected = frozenset(names) if names is not None else None
        with self._lock:
            if self._closed:
                raise RuntimeError("MCP gateway is closed")
            unknown = selected - self._tools.keys() if selected is not None else set()
            if unknown:
                raise ValueError(f"Unknown MCP tools: {sorted(unknown)}")
            if selected != self._selected:
                self._selected = selected
                self._version += 1

    def set_tool_policy(self, policy: Mapping[str, MCPToolPolicy]) -> None:
        updated = {
            server: rule.model_copy(deep=True) for server, rule in policy.items()
        }
        with self._lock:
            if self._closed:
                raise RuntimeError("MCP gateway is closed")
            if updated != self._tool_policy:
                self._tool_policy = updated
                self._version += 1

    def _allows(self, name: str) -> bool:
        server = max(
            (key for key in self._clients if name.startswith(f"{key}.")),
            key=len,
            default=None,
        )
        if server is not None:
            rule = self._tool_policy.get(server)
            if rule is not None and not rule.allows(name[len(server) + 1 :]):
                return False
        return (self._selected is None or name in self._selected) and (
            self._filter is None or bool(self._filter.match(name))
        )

    def list_tools(self, query: str | None = None) -> dict[str, Any]:
        with self._lock:
            tools = [
                {
                    "name": name,
                    "description": tool.mcp_tool.description or "",
                    "input_schema": tool.mcp_tool.input_schema,
                }
                for name, tool in sorted(self._tools.items())
                if self._allows(name)
                and (query is None or query.casefold() in name.casefold())
            ]
            return {"revision": self.revision, "tools": tools}

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        revision: str,
        conversation: "LocalConversation | None",
    ) -> MCPGatewayObservation:
        with self._lock:
            if self._closed:
                return MCPGatewayObservation.from_text(
                    "MCP gateway is closed", is_error=True
                )
            if revision != self.revision:
                return MCPGatewayObservation.from_text(
                    "MCP catalog changed; call list_mcp_tools again.", is_error=True
                )
            if not self._allows(name):
                return MCPGatewayObservation.from_text(
                    "MCP tool is not selected", is_error=True
                )
            tool = self._tools.get(name)
            if tool is None:
                return MCPGatewayObservation.from_text(
                    "MCP tool is unavailable", is_error=True
                )
            try:
                action = tool.action_from_arguments(arguments)
            except ValidationError:
                return MCPGatewayObservation.from_text(
                    "Invalid MCP tool arguments; check list_mcp_tools for the schema.",
                    is_error=True,
                )
            self._active_calls += 1
        try:
            result = tool(action, conversation)
            return MCPGatewayObservation(
                content=result.content, is_error=result.is_error
            )
        except Exception:
            logger.warning("MCP gateway call failed for %s", name)
            return MCPGatewayObservation.from_text(
                "MCP tool call failed", is_error=True
            )
        finally:
            with self._lock:
                self._active_calls -= 1
                self._idle.notify_all()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            while self._active_calls:
                self._idle.wait()
            clients = list(self._clients.values())
            self._clients.clear()
            self._tools.clear()
        for client in clients:
            client.sync_close()
