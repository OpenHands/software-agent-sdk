"""Plugin loading utility for multi-plugin support.

This module provides the canonical function for loading multiple plugins
and merging them into an agent. It is used by:
- LocalConversation (for SDK-direct users)
- ConversationService (for agent-server users)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openhands.sdk.extensions.extensions import Extensions
from openhands.sdk.extensions.sources import from_inline, from_plugin
from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.plugin.plugin import Plugin
from openhands.sdk.plugin.types import PluginSource


if TYPE_CHECKING:
    from openhands.sdk.agent.base import AgentBase


logger = get_logger(__name__)


def load_plugins(
    plugin_specs: list[PluginSource],
    agent: AgentBase,
    max_skills: int = 100,
) -> tuple[AgentBase, HookConfig | None]:
    """Load multiple plugins and merge them into the agent.

    Plugins are loaded in order.  Later plugins in the list have higher
    precedence (last plugin wins for skills and MCP config).  Hooks
    from all plugins are concatenated.

    Args:
        plugin_specs: List of plugin sources to load.
        agent: Agent to merge plugins into.
        max_skills: Maximum total skills allowed (defense-in-depth limit).

    Returns:
        Tuple of (updated_agent, merged_hook_config).
        The agent has updated agent_context (with merged skills) and mcp_config.
        The hook_config contains all hooks from all plugins concatenated.

    Raises:
        PluginFetchError: If any plugin fails to fetch.
        FileNotFoundError: If any plugin fails to load (e.g., path not found).
        ValueError: If max_skills limit is exceeded.

    Example:
        >>> from openhands.sdk.plugin import PluginSource
        >>> plugins = [
        ...     PluginSource(source="github:owner/security-plugin", ref="v1.0.0"),
        ...     PluginSource(source="/local/custom-plugin"),
        ... ]
        >>> updated_agent, hooks = load_plugins(plugins, agent)
    """
    if not plugin_specs:
        return agent, None

    # Build an Extensions bundle for each plugin
    plugin_bundles: list[Extensions] = []
    for spec in plugin_specs:
        logger.info(f"Loading plugin from {spec.source}")
        path = Plugin.fetch(
            source=spec.source,
            ref=spec.ref,
            repo_path=spec.repo_path,
        )
        plugin = Plugin.load(path)
        logger.info(
            f"Loaded plugin '{plugin.name}': "
            f"{len(plugin.skills)} skills, "
            f"hooks={'yes' if plugin.hooks else 'no'}, "
            f"mcp_config={'yes' if plugin.mcp_config else 'no'}"
        )
        plugin_bundles.append(from_plugin(plugin))

    # Agent's existing state is the lowest-precedence base.
    # Later plugins have higher precedence (last-plugin-wins), so we
    # reverse plugin_bundles for the first-wins collapse.
    agent_bundle = from_inline(
        skills=agent.agent_context.skills if agent.agent_context else [],
        mcp_config=agent.mcp_config,
    )
    merged = Extensions.collapse([*reversed(plugin_bundles), agent_bundle])

    # Defense-in-depth skill limit
    if len(merged.skills) > max_skills:
        raise ValueError(
            f"Total skills ({len(merged.skills)}) exceeds maximum ({max_skills})"
        )

    # Apply merged skills + MCP to the agent
    from openhands.sdk.context import AgentContext

    new_context = (
        agent.agent_context.model_copy(update={"skills": merged.skills})
        if agent.agent_context
        else AgentContext(skills=merged.skills)
    )
    updated_agent = agent.model_copy(
        update={
            "agent_context": new_context,
            "mcp_config": merged.mcp_config,
        }
    )

    return updated_agent, merged.hooks
