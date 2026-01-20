"""Plugin loading utility for multi-plugin support.

This module provides the canonical function for loading multiple plugins
and merging them into an agent. It is used by:
- LocalConversation (for SDK-direct users)
- ConversationService (for agent-server users)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.plugin.plugin import Plugin
from openhands.sdk.plugin.types import PluginSource


if TYPE_CHECKING:
    from openhands.sdk.agent.base import AgentBase
    from openhands.sdk.context import AgentContext


logger = get_logger(__name__)


def merge_hook_configs(configs: list[HookConfig]) -> HookConfig | None:
    """Merge multiple hook configs by concatenating handlers per event type.

    Each hook config may have multiple event types (pre_tool_use, post_tool_use, etc.).
    This function combines all matchers from all configs for each event type.

    Args:
        configs: List of HookConfig objects to merge.

    Returns:
        A merged HookConfig with all matchers concatenated, or None if no configs.

    Example:
        >>> config1 = HookConfig(pre_tool_use=[HookMatcher(matcher="*", hooks=[...])])
        >>> config2 = HookConfig(pre_tool_use=[HookMatcher(matcher="terminal", hooks=[...])])
        >>> merged = merge_hook_configs([config1, config2])
        >>> len(merged.pre_tool_use)  # Both matchers combined
        2
    """
    if not configs:
        return None

    # Collect all matchers by event type
    merged_pre_tool_use = []
    merged_post_tool_use = []
    merged_user_prompt_submit = []
    merged_session_start = []
    merged_session_end = []
    merged_stop = []

    for config in configs:
        merged_pre_tool_use.extend(config.pre_tool_use)
        merged_post_tool_use.extend(config.post_tool_use)
        merged_user_prompt_submit.extend(config.user_prompt_submit)
        merged_session_start.extend(config.session_start)
        merged_session_end.extend(config.session_end)
        merged_stop.extend(config.stop)

    merged = HookConfig(
        pre_tool_use=merged_pre_tool_use,
        post_tool_use=merged_post_tool_use,
        user_prompt_submit=merged_user_prompt_submit,
        session_start=merged_session_start,
        session_end=merged_session_end,
        stop=merged_stop,
    )

    # Return None if the merged config is empty
    if merged.is_empty():
        return None

    return merged


def load_plugins(
    plugin_specs: list[PluginSource],
    agent: AgentBase,
    max_skills: int = 100,
) -> tuple[AgentBase, HookConfig | None]:
    """Load multiple plugins and merge them into the agent.

    This is the canonical function for plugin loading, used by:
    - LocalConversation (for SDK-direct users)
    - ConversationService (for agent-server users)

    Plugins are loaded in order and their contents are merged with these semantics:
    - Skills: Override by name (last plugin wins)
    - MCP config: Override by key (last plugin wins)
    - Hooks: Concatenate (all hooks run)

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

    # Start with agent's existing context and MCP config
    merged_context: AgentContext | None = agent.agent_context
    merged_mcp: dict[str, Any] = dict(agent.mcp_config) if agent.mcp_config else {}
    all_hooks: list[HookConfig] = []

    for spec in plugin_specs:
        logger.info(f"Loading plugin from {spec.source}")

        # Fetch (downloads if needed, returns cached path)
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

        # Merge skills and MCP using existing SDK utilities
        merged_context, merged_mcp = plugin.merge_into(
            merged_context,
            merged_mcp,
            max_skills=max_skills,
        )

        # Collect hooks for later combination
        if plugin.hooks and not plugin.hooks.is_empty():
            all_hooks.append(plugin.hooks)

    # Combine all hook configs (concatenation semantics)
    combined_hooks = merge_hook_configs(all_hooks)

    # Create updated agent with merged content
    updated_agent = agent.model_copy(
        update={
            "agent_context": merged_context,
            "mcp_config": merged_mcp,
        }
    )

    return updated_agent, combined_hooks
