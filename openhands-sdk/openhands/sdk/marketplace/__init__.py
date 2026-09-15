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

from typing import Any

from openhands.sdk.marketplace.registration import (
    MarketplaceRegistration as MarketplaceRegistration,
)


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


__all__ = sorted(_TYPE_EXPORTS | _REGISTRY_EXPORTS | {"MarketplaceRegistration"})


def __getattr__(name: str) -> Any:
    if name in _TYPE_EXPORTS:
        from openhands.sdk.marketplace.types import (
            MARKETPLACE_MANIFEST_DIRS,
            MARKETPLACE_MANIFEST_FILE,
            Marketplace,
            MarketplaceEntry,
            MarketplaceMetadata,
            MarketplaceOwner,
            MarketplacePluginEntry,
            MarketplacePluginSource,
        )

        exports = {
            "MARKETPLACE_MANIFEST_DIRS": MARKETPLACE_MANIFEST_DIRS,
            "MARKETPLACE_MANIFEST_FILE": MARKETPLACE_MANIFEST_FILE,
            "Marketplace": Marketplace,
            "MarketplaceEntry": MarketplaceEntry,
            "MarketplaceMetadata": MarketplaceMetadata,
            "MarketplaceOwner": MarketplaceOwner,
            "MarketplacePluginEntry": MarketplacePluginEntry,
            "MarketplacePluginSource": MarketplacePluginSource,
        }
    elif name in _REGISTRY_EXPORTS:
        from openhands.sdk.marketplace.registry import (
            AmbiguousPluginError,
            FetchedMarketplace,
            MarketplaceNotFoundError,
            MarketplaceRegistry,
            PluginNotFoundError,
            PluginResolutionError,
        )

        exports = {
            "AmbiguousPluginError": AmbiguousPluginError,
            "FetchedMarketplace": FetchedMarketplace,
            "MarketplaceNotFoundError": MarketplaceNotFoundError,
            "MarketplaceRegistry": MarketplaceRegistry,
            "PluginNotFoundError": PluginNotFoundError,
            "PluginResolutionError": PluginResolutionError,
        }
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = exports[name]
    globals()[name] = value
    return value
