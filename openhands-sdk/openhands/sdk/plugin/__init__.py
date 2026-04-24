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

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


if TYPE_CHECKING:
    from openhands.sdk.marketplace import (
        Marketplace,
        MarketplaceEntry,
        MarketplaceMetadata,
        MarketplaceOwner,
        MarketplacePluginEntry,
        MarketplacePluginSource,
    )

    from .fetch import PluginFetchError, fetch_plugin_with_resolution
    from .installed import (
        InstalledPluginInfo,
        InstalledPluginsMetadata,
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
    from .loader import load_plugins
    from .plugin import Plugin
    from .source import (
        GitHubURLComponents,
        is_local_path,
        parse_github_url,
        resolve_source_path,
        validate_source_path,
    )
    from .types import (
        CommandDefinition,
        PluginAuthor,
        PluginManifest,
        PluginSource,
        ResolvedPluginSource,
    )


_DEPRECATED_MARKETPLACE_IMPORTS = {
    "Marketplace": ("openhands.sdk.marketplace", "Marketplace"),
    "MarketplaceEntry": ("openhands.sdk.marketplace", "MarketplaceEntry"),
    "MarketplaceMetadata": ("openhands.sdk.marketplace", "MarketplaceMetadata"),
    "MarketplaceOwner": ("openhands.sdk.marketplace", "MarketplaceOwner"),
    "MarketplacePluginEntry": (
        "openhands.sdk.marketplace",
        "MarketplacePluginEntry",
    ),
    "MarketplacePluginSource": (
        "openhands.sdk.marketplace",
        "MarketplacePluginSource",
    ),
}

_LAZY_IMPORTS = {
    "Plugin": (".plugin", "Plugin"),
    "PluginFetchError": (".fetch", "PluginFetchError"),
    "fetch_plugin_with_resolution": (".fetch", "fetch_plugin_with_resolution"),
    "InstalledPluginInfo": (".installed", "InstalledPluginInfo"),
    "InstalledPluginsMetadata": (".installed", "InstalledPluginsMetadata"),
    "disable_plugin": (".installed", "disable_plugin"),
    "enable_plugin": (".installed", "enable_plugin"),
    "get_installed_plugin": (".installed", "get_installed_plugin"),
    "get_installed_plugins_dir": (".installed", "get_installed_plugins_dir"),
    "install_plugin": (".installed", "install_plugin"),
    "list_installed_plugins": (".installed", "list_installed_plugins"),
    "load_installed_plugins": (".installed", "load_installed_plugins"),
    "uninstall_plugin": (".installed", "uninstall_plugin"),
    "update_plugin": (".installed", "update_plugin"),
    "load_plugins": (".loader", "load_plugins"),
    "GitHubURLComponents": (".source", "GitHubURLComponents"),
    "is_local_path": (".source", "is_local_path"),
    "parse_github_url": (".source", "parse_github_url"),
    "resolve_source_path": (".source", "resolve_source_path"),
    "validate_source_path": (".source", "validate_source_path"),
    "CommandDefinition": (".types", "CommandDefinition"),
    "PluginAuthor": (".types", "PluginAuthor"),
    "PluginManifest": (".types", "PluginManifest"),
    "PluginSource": (".types", "PluginSource"),
    "ResolvedPluginSource": (".types", "ResolvedPluginSource"),
}

__all__ = [
    "Plugin",
    "PluginFetchError",
    "PluginManifest",
    "PluginAuthor",
    "PluginSource",
    "ResolvedPluginSource",
    "CommandDefinition",
    "load_plugins",
    "fetch_plugin_with_resolution",
    "Marketplace",
    "MarketplaceEntry",
    "MarketplaceOwner",
    "MarketplacePluginEntry",
    "MarketplacePluginSource",
    "MarketplaceMetadata",
    "GitHubURLComponents",
    "parse_github_url",
    "is_local_path",
    "validate_source_path",
    "resolve_source_path",
    "InstalledPluginInfo",
    "InstalledPluginsMetadata",
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


def __getattr__(name: str) -> Any:
    if name in _DEPRECATED_MARKETPLACE_IMPORTS:
        from openhands.sdk.utils.deprecation import warn_deprecated

        warn_deprecated(
            f"Importing {name} from openhands.sdk.plugin",
            deprecated_in="1.16.0",
            removed_in="1.19.0",
            details="Import from openhands.sdk.marketplace instead.",
            stacklevel=3,
        )
        module_name, attr_name = _DEPRECATED_MARKETPLACE_IMPORTS[name]
        value = getattr(import_module(module_name), attr_name)
        globals()[name] = value
        return value
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
