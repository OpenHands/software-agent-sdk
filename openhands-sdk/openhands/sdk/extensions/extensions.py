"""Extensions — the canonical resolved-extensions container.

An ``Extensions`` object holds the fully-loaded set of skills, hooks,
MCP servers, and agent definitions from any number of sources.
Extensions are merged via a binary ``merge()`` operation with
well-defined per-field semantics so that callers can control precedence
simply by choosing the merge order.
"""

from __future__ import annotations

from functools import reduce
from typing import Any

from pydantic import BaseModel, Field

from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.skills.skill import Skill
from openhands.sdk.subagent.schema import AgentDefinition


logger = get_logger(__name__)


class Extensions(BaseModel):
    """Resolved extensions bundle.

    Immutable container for the four extension types.  Constructed by
    source functions and combined via :py:meth:`merge` /
    :py:meth:`collapse`.

    Merge semantics (``self.merge(other)``):

    * **skills** — first-wins by ``skill.name`` (``self`` kept on collision)
    * **hooks** — concatenate (``self`` hooks run before ``other`` hooks)
    * **mcp_config** — first-wins by server name inside ``mcpServers``,
      ``self`` kept for other top-level keys on collision
    * **agents** — first-wins by ``agent.name`` (``self`` kept on collision)

    Because every keyed field is first-wins, :py:meth:`collapse` expects
    the list ordered from **highest to lowest** precedence (first entry
    wins).
    """

    model_config = {"frozen": True}

    skills: list[Skill] = Field(
        default_factory=list,
        description="Skills loaded from any source.",
    )
    hooks: HookConfig | None = Field(
        default=None,
        description="Merged hook configuration.",
    )
    mcp_config: dict[str, Any] = Field(
        default_factory=dict,
        description="MCP server configuration (mcpServers dict shape).",
    )
    agents: list[AgentDefinition] = Field(
        default_factory=list,
        description="Subagent definitions.",
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        parts: list[str] = []
        if self.skills:
            names = [s.name for s in self.skills]
            parts.append(f"skills={names}")
        if self.hooks is not None:
            n = sum(
                len(getattr(self.hooks, f))
                for f in self.hooks.model_fields
                if isinstance(getattr(self.hooks, f), list)
            )
            parts.append(f"hooks={n} matcher(s)")
        if self.mcp_config:
            servers = list(self.mcp_config.get("mcpServers", {}).keys())
            parts.append(f"mcp={servers}")
        if self.agents:
            names = [a.name for a in self.agents]
            parts.append(f"agents={names}")
        return f"Extensions({', '.join(parts)})" if parts else "Extensions(empty)"

    @classmethod
    def empty(cls) -> Extensions:
        """Return an empty bundle (identity element for :py:meth:`merge`)."""
        return cls()

    def is_empty(self) -> bool:
        """Return ``True`` when every field is empty / ``None``."""
        return (
            not self.skills
            and self.hooks is None
            and not self.mcp_config
            and not self.agents
        )

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge(self, other: Extensions) -> Extensions:
        """Merge *other* into *self*, returning a new bundle.

        *self* is the higher-precedence source (first-wins) for skills,
        MCP config, and agents.  Hooks are concatenated so that *self*'s
        hooks run before *other*'s.
        """
        return Extensions(
            skills=_merge_skills(self.skills, other.skills),
            hooks=_merge_hooks(self.hooks, other.hooks),
            mcp_config=_merge_mcp_config(self.mcp_config, other.mcp_config),
            agents=_merge_agents(self.agents, other.agents),
        )

    @classmethod
    def collapse(cls, bundles: list[Extensions]) -> Extensions:
        """Merge an ordered list of bundles (left = highest precedence).

        Returns :py:meth:`empty` when *bundles* is empty.
        """
        if not bundles:
            return cls.empty()
        return reduce(lambda acc, ext: acc.merge(ext), bundles)


# ======================================================================
# Per-field merge helpers (module-private)
# ======================================================================


def _merge_skills(base: list[Skill], other: list[Skill]) -> list[Skill]:
    """First-wins merge keyed on ``skill.name``.  *base* kept on collision."""
    merged: dict[str, Skill] = {s.name: s for s in base}
    for skill in other:
        if skill.name in merged:
            logger.debug(
                "Skill '%s' already present; keeping higher-precedence definition",
                skill.name,
            )
        else:
            merged[skill.name] = skill
    return list(merged.values())


def _merge_hooks(
    base: HookConfig | None, override: HookConfig | None
) -> HookConfig | None:
    """Concatenate: *base* hooks run before *override* hooks."""
    configs = [c for c in (base, override) if c is not None]
    return HookConfig.merge(configs)


def _merge_mcp_config(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """First-wins merge for MCP config.  *base* kept on collision.

    ``mcpServers`` is merged by server name; other top-level keys are
    kept from *base* when present.
    """
    if not base and not other:
        return {}
    if not base:
        return dict(other)
    if not other:
        return dict(base)

    result = dict(base)

    # Merge mcpServers by server name — base wins on collision
    if "mcpServers" in other:
        existing_servers: dict[str, Any] = result.get("mcpServers", {})
        for server_name, server_cfg in other["mcpServers"].items():
            if server_name in existing_servers:
                logger.debug(
                    "MCP server '%s' already present; keeping"
                    " higher-precedence definition",
                    server_name,
                )
            else:
                existing_servers[server_name] = server_cfg
        result["mcpServers"] = existing_servers

    # Other top-level keys: base wins on collision
    for key, value in other.items():
        if key != "mcpServers" and key not in result:
            result[key] = value

    return result


def _merge_agents(
    base: list[AgentDefinition], other: list[AgentDefinition]
) -> list[AgentDefinition]:
    """First-wins merge keyed on ``agent.name``.  *base* kept on collision."""
    merged: dict[str, AgentDefinition] = {a.name: a for a in base}
    for agent in other:
        if agent.name in merged:
            logger.debug(
                "Agent '%s' already present; keeping higher-precedence definition",
                agent.name,
            )
        else:
            merged[agent.name] = agent
    return list(merged.values())
