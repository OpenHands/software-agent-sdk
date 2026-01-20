"""Utilities for plugin operations."""

from __future__ import annotations

from typing import Any

from openhands.sdk.context import AgentContext
from openhands.sdk.context.skills import Skill
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def merge_skills(
    agent_context: AgentContext | None,
    plugin_skills: list[Skill],
    max_skills: int | None = None,
) -> AgentContext:
    """Merge plugin skills into agent context.

    Plugin skills override existing skills with the same name.

    Args:
        agent_context: Existing agent context (or None)
        plugin_skills: Skills to merge in
        max_skills: Optional max total skills (raises ValueError if exceeded)

    Returns:
        New AgentContext with merged skills

    Raises:
        ValueError: If max_skills limit would be exceeded
    """
    existing_skills = agent_context.skills if agent_context else []

    skills_by_name = {s.name: s for s in existing_skills}
    for skill in plugin_skills:
        if skill.name in skills_by_name:
            logger.warning(f"Plugin skill '{skill.name}' overrides existing skill")
        skills_by_name[skill.name] = skill

    if max_skills is not None and len(skills_by_name) > max_skills:
        raise ValueError(
            f"Total skills ({len(skills_by_name)}) exceeds maximum ({max_skills})"
        )

    merged_skills = list(skills_by_name.values())

    if agent_context:
        return agent_context.model_copy(update={"skills": merged_skills})
    return AgentContext(skills=merged_skills)


def merge_mcp_configs(
    base_config: dict[str, Any] | None,
    plugin_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge MCP configurations.

    Merge semantics (Claude Code compatible):
    - mcpServers: deep-merge by server name (last plugin wins for same server)
    - Other top-level keys: shallow override (plugin wins)

    We special-case mcpServers because it's keyed by server name and multiple
    plugins should be able to contribute different servers. Other keys get
    shallow override because we don't know their semantics - a generic deep-merge
    would be wrong if a key is meant to be atomic. If Claude Code adds new
    nested structures that need merge-by-key (like mcpServers), add them here.

    Args:
        base_config: Base MCP configuration
        plugin_config: Plugin MCP configuration

    Returns:
        Merged configuration (empty dict if both are None)
    """
    if base_config is None and plugin_config is None:
        return {}
    if base_config is None:
        return dict(plugin_config) if plugin_config else {}
    if plugin_config is None:
        return dict(base_config)

    # Shallow copy to avoid mutating inputs
    result = dict(base_config)

    # Merge mcpServers by server name (Claude Code compatible behavior)
    if "mcpServers" in plugin_config:
        existing_servers = result.get("mcpServers", {})
        for server_name in plugin_config["mcpServers"]:
            if server_name in existing_servers:
                logger.warning(
                    f"Plugin MCP server '{server_name}' overrides existing server"
                )
        result["mcpServers"] = {
            **existing_servers,
            **plugin_config["mcpServers"],
        }

    # Other top-level keys: plugin wins (shallow override)
    for key, value in plugin_config.items():
        if key != "mcpServers":
            if key in result:
                logger.warning(
                    f"Plugin MCP config key '{key}' overrides existing value"
                )
            result[key] = value

    return result
