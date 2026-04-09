"""Extensions module for OpenHands SDK.

This module provides shared infrastructure for installable extensions
(plugins, skills, etc.), including:

- Source specification types for describing where to fetch extensions
- Catalog entry types for extension marketplaces
- Generic installation management infrastructure
- Fetching utilities for remote sources

The types and utilities here are used by the plugin, skills, and marketplace
modules to provide consistent behavior for extension management.

Example:
    >>> from openhands.sdk.extensions import ExtensionSource, InstalledExtensionManager
    >>> source = ExtensionSource(source="github:owner/repo", ref="v1.0.0")
"""

from openhands.sdk.extensions.catalog import (
    ExtensionAuthor,
    ExtensionCatalogEntry,
)
from openhands.sdk.extensions.fetch import (
    DEFAULT_CACHE_DIR,
    ExtensionFetchError,
    fetch_extension,
    fetch_extension_with_resolution,
    get_cache_path,
    parse_extension_source,
)
from openhands.sdk.extensions.installed import (
    InstalledExtensionInfo,
    InstalledExtensionManager,
    InstalledExtensionMetadata,
)
from openhands.sdk.extensions.source import (
    ExtensionSource,
    ResolvedExtensionSource,
)


__all__ = [
    # Source types
    "ExtensionSource",
    "ResolvedExtensionSource",
    # Catalog types
    "ExtensionAuthor",
    "ExtensionCatalogEntry",
    # Installed extension management
    "InstalledExtensionInfo",
    "InstalledExtensionMetadata",
    "InstalledExtensionManager",
    # Fetching utilities
    "ExtensionFetchError",
    "fetch_extension",
    "fetch_extension_with_resolution",
    "parse_extension_source",
    "get_cache_path",
    "DEFAULT_CACHE_DIR",
]
