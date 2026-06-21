"""Marketplace module for OpenHands SDK.

This module provides support for plugin and skill marketplaces - directories
that list available plugins and skills with their metadata and source locations.

A marketplace is defined by a `marketplace.json` file in a `.plugin/` or
`.claude-plugin/` directory at the root of a repository. It lists plugins and
skills available for installation, along with metadata like descriptions,
versions, and authors.

Example marketplace.json:
```json
{
    "name": "company-tools",
    "owner": {"name": "DevTools Team"},
    "plugins": [
        {"name": "formatter", "source": "./plugins/formatter"}
    ],
    "skills": [
        {"name": "github", "source": "./skills/github"}
    ]
}
```
"""

from importlib import import_module
from typing import Any

from openhands.sdk.marketplace.registration import MarketplaceRegistration


_TYPE_EXPORTS = {
    "MARKETPLACE_MANIFEST_DIRS",
    "MARKETPLACE_MANIFEST_FILE",
    "Marketplace",
    "MarketplaceEntry",
    "MarketplaceMetadata",
    "MarketplaceOwner",
    "MarketplacePluginEntry",
    "MarketplacePluginSource",
}
_REGISTRY_EXPORTS = {
    "AmbiguousPluginError",
    "FetchedMarketplace",
    "MarketplaceNotFoundError",
    "MarketplaceRegistry",
    "PluginNotFoundError",
    "PluginResolutionError",
}


__all__ = [
    # Constants
    "MARKETPLACE_MANIFEST_DIRS",
    "MARKETPLACE_MANIFEST_FILE",
    # Marketplace classes
    "Marketplace",
    "MarketplaceEntry",
    "MarketplaceOwner",
    "MarketplacePluginEntry",
    "MarketplacePluginSource",
    "MarketplaceMetadata",
    "MarketplaceRegistration",
    # Marketplace registry
    "FetchedMarketplace",
    "MarketplaceRegistry",
    "PluginResolutionError",
    "AmbiguousPluginError",
    "PluginNotFoundError",
    "MarketplaceNotFoundError",
]


def __getattr__(name: str) -> Any:
    if name in _TYPE_EXPORTS:
        return getattr(import_module("openhands.sdk.marketplace.types"), name)
    if name in _REGISTRY_EXPORTS:
        return getattr(import_module("openhands.sdk.marketplace.registry"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
