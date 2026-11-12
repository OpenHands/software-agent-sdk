"""Conversation-scoped built-in tools for the versioned MCP catalog."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import Field

from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.mcp.gateway import MCPToolGateway


class MCPGatewayObservation(Observation):
    """Observation from either stable MCP gateway tool."""


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
        conversation: LocalConversation | None = None,  # noqa: ARG002
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
        self, action: CallMCPToolAction, conversation: LocalConversation | None = None
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
        conv_state: ConversationState | None = None,
    ) -> Sequence[Self]:
        if conv_state is None or conv_state._mcp_gateway is None:
            raise ValueError("ListMCPToolsTool requires an active MCP gateway")
        gateway = conv_state._mcp_gateway
        tool = cls(
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
        if getattr(conv_state.agent, "mcp_gateway_startup_catalog", False):
            tool = tool.model_copy(
                update={
                    "prompt_guidance": (
                        "<MCP_CATALOG>\n"
                        + json.dumps(gateway.list_tools())
                        + "\n</MCP_CATALOG>\n"
                        "Call list_mcp_tools again if this catalog changes."
                    )
                }
            )
        return [tool]


class CallMCPToolTool(ToolDefinition[CallMCPToolAction, MCPGatewayObservation]):
    name: ClassVar[str] = "call_mcp_tool"
    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,
    ) -> Sequence[Self]:
        if conv_state is None or conv_state._mcp_gateway is None:
            raise ValueError("CallMCPToolTool requires an active MCP gateway")
        return [
            cls(
                description=(
                    "Invoke a selected downstream MCP tool using its name, arguments "
                    "and current revision from list_mcp_tools. Calls can modify "
                    "external systems; apply the downstream tool's security policy."
                ),
                action_type=CallMCPToolAction,
                observation_type=MCPGatewayObservation,
                executor=CallMCPToolExecutor(conv_state._mcp_gateway),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
            )
        ]
