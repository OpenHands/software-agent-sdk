"""Bound a sub-agent by the tools and MCP servers of the agent delegating to it."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict

from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.tool.defaults import (
    SUB_AGENT_TOOL_NAME,
    canonical_tool_name,
    effective_builtin_class,
)
from openhands.sdk.tool.registry import registered_tool_class
from openhands.sdk.tool.spec import Tool


if TYPE_CHECKING:
    from openhands.sdk.agent.base import AgentBase


SUB_AGENT_SCOPE_PARAM: Final = "sub_agent_scope"

DELEGATION_TOOL_NAMES: Final = (SUB_AGENT_TOOL_NAME, "workflow_tool_set", "workflow")
"""Tools that start sub-agents and take a ``sub_agent_scope`` parameter."""


class SubAgentScope(BaseModel):
    """Which of the delegating agent's capabilities its sub-agents must stay within."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tools: bool = False
    mcp_servers: bool = False

    @property
    def restricts(self) -> bool:
        return self.tools or self.mcp_servers

    def missing_from(
        self, parent: AgentBase, sub_agent: AgentBase | AgentDefinition
    ) -> list[str]:
        """Return what ``sub_agent`` uses, within this scope, that ``parent`` lacks.

        Conversation-local tools never count: they reach nothing outside the
        sub-agent's own conversation.
        """
        missing: list[str] = []
        if self.tools:
            missing += sorted(
                name
                for name in _tool_names(sub_agent) - _tool_names(parent)
                if not _conversation_local(name)
            )
        if self.mcp_servers:
            missing += [
                f"MCP server {name!r}"
                for name, server in sorted((sub_agent.mcp_config or {}).items())
                if parent.mcp_config.get(name) != server
            ]
        return missing


def scope_delegation_tools(tools: Sequence[Tool], scope: SubAgentScope) -> list[Tool]:
    """Return ``tools`` with ``scope`` added to every delegation tool."""
    if not scope.restricts:
        return list(tools)
    return [
        _narrowed(tool, scope)
        if canonical_tool_name(tool.name) in DELEGATION_TOOL_NAMES
        else tool
        for tool in tools
    ]


def _narrowed(tool: Tool, scope: SubAgentScope) -> Tool:
    current = SubAgentScope.model_validate(tool.params.get(SUB_AGENT_SCOPE_PARAM) or {})
    merged = SubAgentScope(
        tools=scope.tools or current.tools,
        mcp_servers=scope.mcp_servers or current.mcp_servers,
    )
    return tool.model_copy(
        update={"params": {**tool.params, SUB_AGENT_SCOPE_PARAM: merged.model_dump()}}
    )


def _tool_names(agent: AgentBase | AgentDefinition) -> set[str]:
    if isinstance(agent, AgentDefinition):
        names = list(agent.tools)
    else:
        names = [*(tool.name for tool in agent.tools), *agent.include_default_tools]
    return {canonical_tool_name(name) for name in names}


def _conversation_local(name: str) -> bool:
    tool_class = registered_tool_class(name) or effective_builtin_class(name)
    return tool_class is not None and tool_class.conversation_local
