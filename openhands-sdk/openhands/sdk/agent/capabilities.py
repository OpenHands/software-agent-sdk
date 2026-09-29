"""Capability ceilings for configured tools and MCP servers during delegation.

These checks constrain SDK configuration, not arbitrary Python factories, hooks,
or the effects of an allowed tool. They are not a process sandbox.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.tool.spec import Tool


if TYPE_CHECKING:
    from openhands.sdk.agent.agent import Agent
    from openhands.sdk.agent.base import AgentBase


class SubagentCapabilityError(ValueError):
    """A child requests capabilities outside its inherited limits."""


def _fingerprint(config: BaseModel) -> str:
    """Compare actual configuration without persisting or logging credentials."""
    payload = config.model_dump(mode="json", context={"expose_secrets": "plaintext"})
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SubagentCapabilityLimits(BaseModel):
    """Explicit limits; None is unrestricted and an empty tuple denies.

    Names select registered tool specs and MCP servers. Delegation additionally
    seals accepted configuration with fingerprints so lazy initialization and
    restoration cannot replace a same-named capability. Fingerprints are
    sensitive metadata; no plaintext credentials are stored here.
    """

    model_config = ConfigDict(frozen=True)

    tool_names: tuple[str, ...] | None = Field(default=None)
    mcp_server_names: tuple[str, ...] | None = Field(default=None)
    builtin_tool_names: tuple[str, ...] | None = Field(default=None)
    tool_fingerprints: dict[str, str] | None = Field(default=None, repr=False)
    mcp_fingerprints: dict[str, str] | None = Field(default=None, repr=False)
    tool_filter_regex: str | None = Field(default=None)
    enforce_tool_filter: bool = Field(default=False)

    @classmethod
    def from_selection(
        cls,
        *,
        tools: Sequence[Tool] | None,
        mcp_server_refs: Sequence[str] | None,
    ) -> Self:
        """Capture selections before defaulting loses their None values."""
        return cls(
            tool_names=None if tools is None else tuple(t.name for t in tools),
            mcp_server_names=(
                None if mcp_server_refs is None else tuple(mcp_server_refs)
            ),
        )

    def check_builtins(self, names: Sequence[str]) -> None:
        """Reject additional implicit tools before their constructors run."""
        if self.builtin_tool_names is not None:
            for name in names:
                if name not in self.builtin_tool_names:
                    raise SubagentCapabilityError(
                        f"Built-in tool '{name}' is not allowed"
                    )

    def check_mcp(self, servers: dict[str, MCPServer]) -> None:
        """Validate servers before starting a process or opening a connection."""
        if self.mcp_server_names is None:
            return
        for name, server in servers.items():
            if name not in self.mcp_server_names:
                raise SubagentCapabilityError(f"MCP server '{name}' is not allowed")
            if self.mcp_fingerprints is not None and self.mcp_fingerprints.get(
                name
            ) != _fingerprint(server):
                raise SubagentCapabilityError(
                    f"MCP server '{name}' configuration changed"
                )

    def check_agent(self, agent: AgentBase) -> None:
        """Validate configuration without initializing tools or MCP clients."""
        if self.tool_names is not None:
            for tool in agent.tools:
                if tool.name not in self.tool_names:
                    raise SubagentCapabilityError(f"Tool '{tool.name}' is not allowed")
                if self.tool_fingerprints is not None and self.tool_fingerprints.get(
                    tool.name
                ) != _fingerprint(tool):
                    raise SubagentCapabilityError(
                        f"Tool '{tool.name}' configuration changed"
                    )
        self.check_builtins(agent._default_tool_names())
        self.check_mcp(agent.mcp_config)
        if (
            self.enforce_tool_filter
            and agent.filter_tools_regex != self.tool_filter_regex
        ):
            raise SubagentCapabilityError("Inherited tool filter cannot be changed")

    def check_child(self, *, parent: AgentBase, child: AgentBase) -> None:
        """Reject wider names, parameters, MCP credentials, or tool filters."""
        self.check_agent(child)
        if self.tool_names is not None:
            parent_tools = {tool.name: _fingerprint(tool) for tool in parent.tools}
            for tool in child.tools:
                if parent_tools.get(tool.name) != _fingerprint(tool):
                    raise SubagentCapabilityError(
                        f"Tool '{tool.name}' is outside the parent configuration"
                    )
            parent_builtins = set(parent._default_tool_names())
            for name in child._default_tool_names():
                if name not in parent_builtins:
                    raise SubagentCapabilityError(
                        f"Built-in tool '{name}' is not allowed"
                    )
        if self.mcp_server_names is not None:
            for name, server in child.mcp_config.items():
                parent_server = parent.mcp_config.get(name)
                if parent_server is None or _fingerprint(parent_server) != _fingerprint(
                    server
                ):
                    raise SubagentCapabilityError(
                        f"MCP server '{name}' is outside the parent configuration"
                    )
        if (self.tool_names is not None or self.mcp_server_names is not None) and (
            parent.filter_tools_regex is not None
            and child.filter_tools_regex != parent.filter_tools_regex
        ):
            raise SubagentCapabilityError("Child must preserve the parent tool filter")


def prepare_subagent(
    *,
    parent: AgentBase,
    child: Agent,
    limits: SubagentCapabilityLimits | None,
) -> Agent:
    """Validate and seal a child before initialization, including nested limits.

    Missing limits preserve legacy behavior. Explicit violations raise rather
    than silently removing tools. Trusted factories keep their existing API.
    """
    own_limits = child.subagent_capability_limits
    if limits is None:
        return child
    limits.check_agent(parent)
    limits.check_child(parent=parent, child=child)
    if own_limits is not None:
        own_limits.check_agent(child)

    restrict_tools = limits.tool_names is not None or (
        own_limits is not None and own_limits.tool_names is not None
    )
    restrict_mcp = limits.mcp_server_names is not None or (
        own_limits is not None and own_limits.mcp_server_names is not None
    )
    builtins = set(parent._default_tool_names())
    if own_limits is not None and own_limits.builtin_tool_names is not None:
        builtins.intersection_update(own_limits.builtin_tool_names)
    inherited = SubagentCapabilityLimits(
        tool_names=tuple(t.name for t in child.tools) if restrict_tools else None,
        mcp_server_names=tuple(child.mcp_config) if restrict_mcp else None,
        builtin_tool_names=tuple(sorted(builtins)) if restrict_tools else None,
        tool_fingerprints=(
            {t.name: _fingerprint(t) for t in child.tools} if restrict_tools else None
        ),
        mcp_fingerprints=(
            {name: _fingerprint(server) for name, server in child.mcp_config.items()}
            if restrict_mcp
            else None
        ),
        tool_filter_regex=child.filter_tools_regex,
        enforce_tool_filter=restrict_tools or restrict_mcp,
    )
    return child.model_copy(update={"subagent_capability_limits": inherited})
