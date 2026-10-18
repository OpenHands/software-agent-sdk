"""Bound a sub-agent by the agent delegating to it.

A sub-agent always runs with exactly its parent's MCP servers
(:func:`inherit_mcp_config`). A :class:`SubAgentScope` can additionally keep its
tools within the parent's.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.logger import get_logger
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


logger = get_logger(__name__)

SUB_AGENT_SCOPE_PARAM: Final = "sub_agent_scope"

DELEGATION_TOOL_NAMES: Final = (SUB_AGENT_TOOL_NAME, "workflow_tool_set", "workflow")
"""Tools that start sub-agents and take a ``sub_agent_scope`` parameter."""


class SubAgentScope(BaseModel):
    """Which of the delegating agent's tools its sub-agents must stay within.

    MCP servers are not part of the scope: every sub-agent inherits exactly the
    delegating agent's MCP servers (see :func:`inherit_mcp_config`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tools: bool = False
    mcp_servers: bool = Field(
        default=False,
        exclude=True,
        deprecated=True,
        description=(
            "Deprecated and ignored: sub-agents always inherit the delegating "
            "agent's MCP servers. Still accepted so stored tool params load."
        ),
    )

    @property
    def restricts(self) -> bool:
        return self.tools

    def missing_from(
        self, parent: AgentBase, sub_agent: AgentBase | AgentDefinition
    ) -> list[str]:
        """Return the tools ``sub_agent`` uses that ``parent`` lacks, if scoped.

        Conversation-local tools never count: they reach nothing outside the
        sub-agent's own conversation.
        """
        if not self.tools:
            return []
        return sorted(
            name
            for name in _tool_names(sub_agent) - _tool_names(parent)
            if not _conversation_local(name)
        )


def inherit_mcp_config[AgentT: AgentBase](
    parent: AgentBase, sub_agent: AgentT
) -> AgentT:
    """Return ``sub_agent`` with exactly ``parent``'s MCP servers.

    ``parent`` is the delegating conversation's agent, whose ``mcp_config`` is
    already resolved (plugin servers merged, ``${VAR}`` secrets expanded).
    Servers the sub-agent declares itself are dropped, never merged.
    """
    declared = sub_agent.mcp_config
    if declared and declared != parent.mcp_config:
        logger.info(
            "Ignoring the sub-agent's own MCP servers (%s); it inherits its "
            "parent's (%s)",
            ", ".join(sorted(declared)),
            ", ".join(sorted(parent.mcp_config)) or "none",
        )
    return sub_agent.model_copy(update={"mcp_config": dict(parent.mcp_config)})


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
    merged = SubAgentScope(tools=scope.tools or current.tools)
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
