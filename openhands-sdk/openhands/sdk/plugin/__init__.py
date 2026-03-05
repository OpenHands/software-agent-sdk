"""Plugin module for OpenHands SDK.

This module provides support for loading and managing plugins that bundle
skills, hooks, MCP configurations, agents, and commands together.

It also provides support for plugin marketplaces - directories that list
available plugins with their metadata and source locations.

Additionally, it provides utilities for managing installed plugins in the
user's home directory (~/.openhands/plugins/installed/).
"""

from openhands.sdk.plugin.fetch import (
    PluginFetchError,
    fetch_plugin_with_resolution,
)
from openhands.sdk.plugin.installed import (
    InstalledPluginInfo,
    InstalledPluginsMetadata,
    get_installed_plugin,
    get_installed_plugins_dir,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
    uninstall_plugin,
    update_plugin,
)
from openhands.sdk.plugin.loader import load_plugins
from openhands.sdk.plugin.plugin import Plugin
from openhands.sdk.plugin.types import (
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


__all__ = [
    # Plugin classes
    "Plugin",
    "PluginFetchError",
    "PluginManifest",
    "PluginAuthor",
    "PluginSource",
    "ResolvedPluginSource",
    "CommandDefinition",
    # Plugin loading
    "load_plugins",
    "fetch_plugin_with_resolution",
    # Marketplace classes
    "Marketplace",
    "MarketplaceOwner",
    "MarketplacePluginEntry",
    "MarketplacePluginSource",
    "MarketplaceMetadata",
    # Installed plugins management
    "InstalledPluginInfo",
    "InstalledPluginsMetadata",
    "install_plugin",
    "uninstall_plugin",
    "list_installed_plugins",
    "load_installed_plugins",
    "get_installed_plugins_dir",
    "get_installed_plugin",
    "update_plugin",
]
