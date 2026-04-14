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
]
