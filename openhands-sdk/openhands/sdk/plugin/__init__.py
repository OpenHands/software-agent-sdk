"""Plugin module for OpenHands SDK.

This module provides support for loading and managing plugins that bundle
skills, hooks, MCP configurations, agents, and commands together.

It also provides support for plugin marketplaces - directories that list
available plugins with their metadata and source locations.
"""

from openhands.sdk.plugin.fetch import PluginFetchError
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
)


__all__ = [
    # Plugin classes
    "Plugin",
    "PluginFetchError",
    "PluginManifest",
    "PluginAuthor",
    "AgentDefinition",
    "CommandDefinition",
    # Marketplace classes
    "Marketplace",
    "MarketplaceOwner",
    "MarketplacePluginEntry",
    "MarketplacePluginSource",
    "MarketplaceMetadata",
]
