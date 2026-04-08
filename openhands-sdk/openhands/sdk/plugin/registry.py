"""Marketplace registry for managing registered marketplaces and plugin resolution."""

from __future__ import annotations

from pathlib import Path

from openhands.sdk.logger import get_logger
from openhands.sdk.plugin.fetch import fetch_plugin_with_resolution
from openhands.sdk.plugin.types import (
    Marketplace,
    MarketplaceRegistration,
    PluginSource,
)


logger = get_logger(__name__)


class PluginResolutionError(Exception):
    """Raised when a plugin cannot be resolved from registered marketplaces."""

    pass


class AmbiguousPluginError(PluginResolutionError):
    """Raised when a plugin name matches multiple marketplaces."""

    def __init__(self, plugin_name: str, matching_marketplaces: list[str]):
        self.plugin_name = plugin_name
        self.matching_marketplaces = matching_marketplaces
        super().__init__(
            f"Plugin '{plugin_name}' is ambiguous - found in multiple marketplaces: "
            f"{', '.join(matching_marketplaces)}. "
            f"Use explicit format: '{plugin_name}@<marketplace-name>'"
        )


class PluginNotFoundError(PluginResolutionError):
    """Raised when a plugin cannot be found in any registered marketplace."""

    def __init__(
        self,
        plugin_name: str,
        marketplace_name: str | None = None,
        fetch_errors: dict[str, Exception] | None = None,
    ):
        self.plugin_name = plugin_name
        self.marketplace_name = marketplace_name
        self.fetch_errors = fetch_errors or {}

        if marketplace_name:
            msg = (
                f"Plugin '{plugin_name}' not found in marketplace '{marketplace_name}'"
            )
        elif fetch_errors:
            # All marketplaces failed to fetch - show the actual errors
            error_details = "; ".join(
                f"'{name}': {err}" for name, err in fetch_errors.items()
            )
            msg = (
                f"Plugin '{plugin_name}' not found. "
                f"All {len(fetch_errors)} marketplace(s) failed to fetch: "
                f"{error_details}"
            )
        else:
            msg = f"Plugin '{plugin_name}' not found in any registered marketplace"
        super().__init__(msg)


class MarketplaceNotFoundError(PluginResolutionError):
    """Raised when a referenced marketplace is not registered."""

    def __init__(self, marketplace_name: str):
        self.marketplace_name = marketplace_name
        super().__init__(f"Marketplace '{marketplace_name}' is not registered")


class MarketplaceRegistry:
    """Manages registered marketplaces with lazy fetching and plugin resolution.

    The registry stores marketplace registrations and provides:
    - Lazy fetching: Marketplaces are only fetched when first needed
    - Caching: Fetched marketplaces are cached for the session
    - Plugin resolution: Resolve plugin references like 'plugin-name@marketplace'

    Example:
        >>> registry = MarketplaceRegistry([
        ...     MarketplaceRegistration(
        ...         name="public",
        ...         source="github:OpenHands/skills",
        ...         auto_load="all"
        ...     ),
        ...     MarketplaceRegistration(
        ...         name="team",
        ...         source="github:acme/plugins"
        ...     ),
        ... ])
        >>> # Resolve a plugin from a specific marketplace
        >>> source = registry.resolve_plugin("formatter@team")
        >>> # Resolve a plugin, searching all marketplaces
        >>> source = registry.resolve_plugin("git")
    """

    def __init__(self, registrations: list[MarketplaceRegistration] | None = None):
        """Initialize the registry with marketplace registrations.

        Args:
            registrations: List of marketplace registrations. Can be empty or None.
        """
        self._registrations: dict[str, MarketplaceRegistration] = {}
        # Maps name to (marketplace, path)
        self._cache: dict[str, tuple[Marketplace, Path]] = {}

        if registrations:
            for reg in registrations:
                self._registrations[reg.name] = reg

    @property
    def registrations(self) -> dict[str, MarketplaceRegistration]:
        """Get all registered marketplaces."""
        return self._registrations.copy()

    def get_auto_load_registrations(self) -> list[MarketplaceRegistration]:
        """Get registrations with auto_load='all'."""
        return [reg for reg in self._registrations.values() if reg.auto_load == "all"]

    def _fetch_marketplace(
        self, reg: MarketplaceRegistration
    ) -> tuple[Marketplace, Path]:
        """Fetch a marketplace and return (Marketplace, repo_path).

        This is the internal method that does the actual fetching.
        Results are cached to avoid repeated fetches.
        """
        if reg.name in self._cache:
            return self._cache[reg.name]

        logger.info(f"Fetching marketplace '{reg.name}' from {reg.source}")

        # Fetch the marketplace repository
        repo_path, resolved_ref = fetch_plugin_with_resolution(
            source=reg.source,
            ref=reg.ref,
            repo_path=reg.repo_path,
        )

        # Load the marketplace manifest
        marketplace = Marketplace.load(repo_path)

        logger.debug(
            f"Loaded marketplace '{reg.name}' with {len(marketplace.plugins)} plugins"
            + (f" @ {resolved_ref[:8]}" if resolved_ref else "")
        )

        # Cache the result
        self._cache[reg.name] = (marketplace, Path(repo_path))
        return marketplace, Path(repo_path)

    def get_marketplace(self, name: str) -> tuple[Marketplace, Path]:
        """Get a marketplace by name, fetching lazily if needed.

        Args:
            name: The marketplace registration name.

        Returns:
            Tuple of (Marketplace, repo_path).

        Raises:
            MarketplaceNotFoundError: If the marketplace is not registered.
        """
        if name not in self._registrations:
            raise MarketplaceNotFoundError(name)

        return self._fetch_marketplace(self._registrations[name])

    def prefetch_all(self) -> None:
        """Eagerly fetch all registered marketplaces.

        This is useful for validation or pre-warming the cache.
        Any fetch errors are logged but not raised.
        """
        for name, reg in self._registrations.items():
            try:
                self._fetch_marketplace(reg)
            except Exception as e:
                logger.warning(f"Failed to prefetch marketplace '{name}': {e}")

    def _parse_plugin_ref(self, plugin_ref: str) -> tuple[str, str | None]:
        """Parse a plugin reference into (plugin_name, marketplace_name).

        Formats:
        - 'plugin-name' -> ('plugin-name', None)
        - 'plugin-name@marketplace' -> ('plugin-name', 'marketplace')
        """
        if "@" in plugin_ref:
            parts = plugin_ref.rsplit("@", 1)
            return parts[0], parts[1]
        return plugin_ref, None

    def resolve_plugin(self, plugin_ref: str) -> PluginSource:
        """Resolve a plugin reference to a PluginSource.

        Args:
            plugin_ref: Plugin reference in format 'plugin-name' or
                'plugin-name@marketplace-name'.

        Returns:
            PluginSource that can be used to load the plugin.

        Raises:
            PluginNotFoundError: If the plugin is not found.
            AmbiguousPluginError: If the plugin name matches multiple marketplaces.
            MarketplaceNotFoundError: If a specified marketplace is not registered.
        """
        plugin_name, marketplace_name = self._parse_plugin_ref(plugin_ref)

        if marketplace_name:
            # Explicit marketplace specified
            return self._resolve_from_marketplace(plugin_name, marketplace_name)
        else:
            # Search all registered marketplaces
            return self._resolve_from_all(plugin_name)

    def _resolve_from_marketplace(
        self, plugin_name: str, marketplace_name: str
    ) -> PluginSource:
        """Resolve a plugin from a specific marketplace."""
        marketplace, repo_path = self.get_marketplace(marketplace_name)

        plugin_entry = marketplace.get_plugin(plugin_name)
        if plugin_entry is None:
            raise PluginNotFoundError(plugin_name, marketplace_name)

        # Resolve the plugin source
        source, ref, subpath = marketplace.resolve_plugin_source(plugin_entry)

        return PluginSource(
            source=source,
            ref=ref,
            repo_path=subpath,
        )

    def _resolve_from_all(self, plugin_name: str) -> PluginSource:
        """Resolve a plugin by searching all registered marketplaces."""
        matches: list[tuple[str, PluginSource]] = []
        fetch_errors: dict[str, Exception] = {}
        searched_count = 0

        for name, reg in self._registrations.items():
            try:
                marketplace, repo_path = self._fetch_marketplace(reg)
                searched_count += 1
                plugin_entry = marketplace.get_plugin(plugin_name)

                if plugin_entry is not None:
                    source, ref, subpath = marketplace.resolve_plugin_source(
                        plugin_entry
                    )
                    plugin_source = PluginSource(
                        source=source,
                        ref=ref,
                        repo_path=subpath,
                    )
                    matches.append((name, plugin_source))

            except Exception as e:
                fetch_errors[name] = e
                logger.warning(
                    f"Error searching marketplace '{name}' "
                    f"for plugin '{plugin_name}': {e}"
                )

        if not matches:
            # If all marketplaces failed to fetch, include errors in exception
            if fetch_errors and searched_count == 0:
                raise PluginNotFoundError(plugin_name, fetch_errors=fetch_errors)
            raise PluginNotFoundError(plugin_name)

        if len(matches) > 1:
            raise AmbiguousPluginError(
                plugin_name,
                [name for name, _ in matches],
            )

        return matches[0][1]

    def list_plugins(self, marketplace_name: str | None = None) -> list[str]:
        """List available plugins from registered marketplaces.

        Args:
            marketplace_name: If provided, list plugins from this marketplace only.
                If None, list plugins from all registered marketplaces.

        Returns:
            List of plugin names (may include duplicates if searching all).

        Raises:
            PluginResolutionError: If all marketplaces fail to fetch when listing all.
        """
        plugin_names: list[str] = []

        if marketplace_name:
            marketplace, _ = self.get_marketplace(marketplace_name)
            plugin_names.extend(p.name for p in marketplace.plugins)
        else:
            fetch_errors: dict[str, Exception] = {}
            for name, reg in self._registrations.items():
                try:
                    marketplace, _ = self._fetch_marketplace(reg)
                    plugin_names.extend(p.name for p in marketplace.plugins)
                except Exception as e:
                    fetch_errors[name] = e
                    logger.warning(f"Error listing plugins from '{name}': {e}")

            # If all marketplaces failed, raise with details
            if fetch_errors and not plugin_names and self._registrations:
                error_details = "; ".join(
                    f"'{name}': {err}" for name, err in fetch_errors.items()
                )
                raise PluginResolutionError(
                    f"Failed to list plugins. "
                    f"All {len(fetch_errors)} marketplace(s) failed: {error_details}"
                )

        return plugin_names
