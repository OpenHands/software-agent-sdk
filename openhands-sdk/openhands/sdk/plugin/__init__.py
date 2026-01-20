"""Plugin module for OpenHands SDK.

This module provides support for loading and managing plugins that bundle
skills, hooks, MCP configurations, agents, and commands together.

It also provides support for plugin marketplaces - directories that list
available plugins with their metadata and source locations.
"""

from openhands.sdk.plugin.fetch import (
    PluginFetchError,
    fetch_plugin_with_resolution,
)
from openhands.sdk.plugin.loader import load_plugins
from openhands.sdk.plugin.plugin import Plugin
from openhands.sdk.plugin.types import (
    AgentDefinition,
    CommandDefinition,
    Marketplace,
    MarketplaceMetadata,
    MarketplaceOwner,
    MarketplacePluginEntry,
    MarketplacePluginSource,
    PluginAuthor,
    PluginManifest,
    PluginSource,
    ResolvedPluginSource,
)
from openhands.sdk.plugin.utils import merge_mcp_configs, merge_skills


__all__ = [
    # Plugin classes
    "Plugin",
    "PluginFetchError",
    "PluginManifest",
    "PluginAuthor",
    "PluginSource",
    "ResolvedPluginSource",
    "AgentDefinition",
    "CommandDefinition",
    # Plugin loading
    "load_plugins",
    "fetch_plugin_with_resolution",
    "merge_mcp_configs",
    "merge_skills",
    # Marketplace classes
    "Marketplace",
    "MarketplaceOwner",
    "MarketplacePluginEntry",
    "MarketplacePluginSource",
    "MarketplaceMetadata",
]
