"""Two stable agent tools over a versioned, conversation-local MCP catalog."""

import json
import threading
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import Field, ValidationError

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer, enabled_mcp_servers
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.mcp.utils import (
    MCPToolProvider,
    provider_supports_on_tools_reconciled,
)
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


logger = get_logger(__name__)
_GATEWAY_STARTUP_TIMEOUT = 30.0


class MCPGatewayObservation(Observation):
    """Observation from either stable MCP gateway tool."""


class MCPToolGateway:
    """Own downstream sessions; expose selected tools without registering them."""

    def __init__(self, selected_tools: Sequence[str] | None = None):
        self._lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._active_calls = 0
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, MCPToolDefinition] = {}
        self._selected = (
            frozenset(selected_tools) if selected_tools is not None else None
        )
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

    def list_tools(self, query: str | None = None) -> dict[str, Any]:
        with self._lock:
            selected = self._selected
            tools = [
                {
                    "name": name,
                    "description": tool.mcp_tool.description or "",
                    "input_schema": tool.mcp_tool.input_schema,
                }
                for name, tool in sorted(self._tools.items())
                if (selected is None or name in selected)
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
            if self._selected is not None and name not in self._selected:
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


class ListMCPToolsAction(Action):
    query: str | None = Field(
        default=None, description="Optional tool-name substring to search."
    )


class CallMCPToolAction(Action):
    name: str = Field(description="Namespaced tool name returned by list_mcp_tools.")
    arguments: dict[str, Any] = Field(
        description="Arguments matching its input_schema."
    )
    revision: str = Field(description="Catalog revision returned by list_mcp_tools.")


class ListMCPToolsExecutor(ToolExecutor[ListMCPToolsAction, MCPGatewayObservation]):
    def __init__(self, gateway: MCPToolGateway):
        self.gateway = gateway

    def __call__(
        self,
        action: ListMCPToolsAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> MCPGatewayObservation:
        return MCPGatewayObservation.from_text(
            json.dumps(self.gateway.list_tools(action.query))
        )

    def close(self) -> None:
        self.gateway.close()


class CallMCPToolExecutor(ToolExecutor[CallMCPToolAction, MCPGatewayObservation]):
    def __init__(self, gateway: MCPToolGateway):
        self.gateway = gateway

    def __call__(
        self, action: CallMCPToolAction, conversation: "LocalConversation | None" = None
    ) -> MCPGatewayObservation:
        return self.gateway.call(
            action.name, action.arguments, action.revision, conversation
        )

    def close(self) -> None:
        self.gateway.close()


class ListMCPToolsTool(ToolDefinition[ListMCPToolsAction, MCPGatewayObservation]):
    name: ClassVar[str] = "list_mcp_tools"
    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        *,
        gateway: MCPToolGateway,
    ) -> Sequence[Self]:
        return [
            cls(
                description=(
                    "List selected MCP tools with their current input schemas and "
                    "catalog revision. Call before call_mcp_tool and again if the "
                    "selection or server catalog changes."
                ),
                action_type=ListMCPToolsAction,
                observation_type=MCPGatewayObservation,
                executor=ListMCPToolsExecutor(gateway),
                annotations=ToolAnnotations(readOnlyHint=True),
            )
        ]


class CallMCPToolTool(ToolDefinition[CallMCPToolAction, MCPGatewayObservation]):
    name: ClassVar[str] = "call_mcp_tool"
    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        *,
        gateway: MCPToolGateway,
    ) -> Sequence[Self]:
        return [
            cls(
                description=(
                    "Invoke a selected downstream MCP tool using its name, arguments "
                    "and current revision from list_mcp_tools. Calls can modify "
                    "external systems; apply the downstream tool's security policy."
                ),
                action_type=CallMCPToolAction,
                observation_type=MCPGatewayObservation,
                executor=CallMCPToolExecutor(gateway),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
            )
        ]
