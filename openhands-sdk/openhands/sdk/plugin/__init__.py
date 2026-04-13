"""Plugin module for OpenHands SDK.

This module provides support for loading and managing plugins that bundle
skills, hooks, MCP configurations, agents, and commands together.

It also provides support for plugin marketplaces - directories that list
available plugins with their metadata and source locations.

Additionally, it provides utilities for managing installed plugins in the
user's home directory (~/.openhands/plugins/installed/).

Note: Marketplace classes have been moved to ``openhands.sdk.marketplace``.
They are still importable from this module for backward compatibility, but
importing them from here will emit a deprecation warning.
"""

from typing import Any

# Import marketplace classes from new location for internal use
# (no deprecation warning since we're importing from the canonical location)
from openhands.sdk.marketplace import (
    Marketplace as _Marketplace,
    MarketplaceEntry as _MarketplaceEntry,
    MarketplaceMetadata as _MarketplaceMetadata,
    MarketplaceOwner as _MarketplaceOwner,
    MarketplacePluginEntry as _MarketplacePluginEntry,
    MarketplacePluginSource as _MarketplacePluginSource,
)
from openhands.sdk.plugin.fetch import (
    PluginFetchError,
    fetch_plugin_with_resolution,
)
from openhands.sdk.plugin.installed import (
    InstalledPluginInfo,
    disable_plugin,
    enable_plugin,
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
from openhands.sdk.plugin.source import (
    GitHubURLComponents,
    is_local_path,
    parse_github_url,
    resolve_source_path,
    validate_source_path,
)
from openhands.sdk.plugin.types import (
    CommandDefinition,
    PluginAuthor,
    PluginManifest,
    PluginSource,
    ResolvedPluginSource,
)


# Deprecated marketplace names that trigger warnings when accessed
_DEPRECATED_MARKETPLACE_NAMES = {
    "Marketplace": _Marketplace,
    "MarketplaceEntry": _MarketplaceEntry,
    "MarketplaceMetadata": _MarketplaceMetadata,
    "MarketplaceOwner": _MarketplaceOwner,
    "MarketplacePluginEntry": _MarketplacePluginEntry,
    "MarketplacePluginSource": _MarketplacePluginSource,
}


def __getattr__(name: str) -> Any:
    """Provide deprecated marketplace names with warnings."""
    if name in _DEPRECATED_MARKETPLACE_NAMES:
        from openhands.sdk.utils.deprecation import warn_deprecated

        warn_deprecated(
            f"Importing {name} from openhands.sdk.plugin",
            deprecated_in="1.16.0",
            removed_in="1.19.0",
            details="Import from openhands.sdk.marketplace instead.",
            stacklevel=3,
        )
        return _DEPRECATED_MARKETPLACE_NAMES[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    # Marketplace classes (deprecated - import from openhands.sdk.marketplace)
    "Marketplace",
    "MarketplaceEntry",
    "MarketplaceOwner",
    "MarketplacePluginEntry",
    "MarketplacePluginSource",
    "MarketplaceMetadata",
    # Source path utilities
    "GitHubURLComponents",
    "parse_github_url",
    "is_local_path",
    "validate_source_path",
    "resolve_source_path",
    # Installed plugins management
    "InstalledPluginInfo",
    "install_plugin",
    "uninstall_plugin",
    "list_installed_plugins",
    "load_installed_plugins",
    "get_installed_plugins_dir",
    "get_installed_plugin",
    "enable_plugin",
    "disable_plugin",
    "update_plugin",
]
