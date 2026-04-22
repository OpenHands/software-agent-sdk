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

    * **skills** — last-wins by ``skill.name`` (``other`` overrides ``self``)
    * **hooks** — concatenate (``self`` hooks run before ``other`` hooks)
    * **mcp_config** — last-wins by server name inside ``mcpServers``,
      shallow override for other top-level keys
    * **agents** — first-wins by ``agent.name`` (``self`` kept on collision)
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

        *other* is treated as the higher-precedence source for skills and
        MCP config (last-wins).  For agents, *self* has priority
        (first-wins).  Hooks are concatenated so that *self*'s hooks run
        before *other*'s.
        """
        return Extensions(
            skills=_merge_skills(self.skills, other.skills),
            hooks=_merge_hooks(self.hooks, other.hooks),
            mcp_config=_merge_mcp_config(self.mcp_config, other.mcp_config),
            agents=_merge_agents(self.agents, other.agents),
        )

    @classmethod
    def collapse(cls, bundles: list[Extensions]) -> Extensions:
        """Merge an ordered list of bundles (left = lowest precedence).

        Returns :py:meth:`empty` when *bundles* is empty.
        """
        if not bundles:
            return cls.empty()
        return reduce(lambda acc, ext: acc.merge(ext), bundles)


# ======================================================================
# Per-field merge helpers (module-private)
# ======================================================================


def _merge_skills(base: list[Skill], override: list[Skill]) -> list[Skill]:
    """Last-wins merge keyed on ``skill.name``."""
    merged: dict[str, Skill] = {s.name: s for s in base}
    for skill in override:
        if skill.name in merged:
            logger.debug(
                "Skill '%s' overridden by higher-precedence source", skill.name
            )
        merged[skill.name] = skill
    return list(merged.values())


def _merge_hooks(
    base: HookConfig | None, override: HookConfig | None
) -> HookConfig | None:
    """Concatenate: *base* hooks run before *override* hooks."""
    configs = [c for c in (base, override) if c is not None]
    return HookConfig.merge(configs)


def _merge_mcp_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Last-wins merge for MCP config.

    ``mcpServers`` is deep-merged by server name; other top-level keys
    are shallow-overridden.
    """
    if not base and not override:
        return {}
    if not base:
        return dict(override)
    if not override:
        return dict(base)

    result = dict(base)

    # Deep-merge mcpServers by server name
    if "mcpServers" in override:
        existing_servers: dict[str, Any] = result.get("mcpServers", {})
        for server_name in override["mcpServers"]:
            if server_name in existing_servers:
                logger.debug(
                    "MCP server '%s' overridden by higher-precedence source",
                    server_name,
                )
        result["mcpServers"] = {
            **existing_servers,
            **override["mcpServers"],
        }

    # Other top-level keys: shallow override
    for key, value in override.items():
        if key != "mcpServers":
            if key in result:
                logger.debug(
                    "MCP config key '%s' overridden by higher-precedence source",
                    key,
                )
            result[key] = value

    return result


def _merge_agents(
    base: list[AgentDefinition], override: list[AgentDefinition]
) -> list[AgentDefinition]:
    """First-wins merge keyed on ``agent.name``."""
    merged: dict[str, AgentDefinition] = {a.name: a for a in base}
    for agent in override:
        if agent.name in merged:
            logger.debug(
                "Agent '%s' already present; keeping earlier definition (first-wins)",
                agent.name,
            )
        else:
            merged[agent.name] = agent
    return list(merged.values())
