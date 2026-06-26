"""Marketplace registry for lazy marketplace loading and plugin resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openhands.sdk.logger import get_logger
from openhands.sdk.marketplace.registration import MarketplaceRegistration
from openhands.sdk.marketplace.types import Marketplace
from openhands.sdk.plugin.fetch import fetch_plugin_with_resolution
from openhands.sdk.plugin.types import PluginSource
from openhands.sdk.utils.redact import redact_url_credentials


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FetchedMarketplace:
    marketplace: Marketplace
    path: Path
    resolved_ref: str | None


class PluginResolutionError(Exception):
    """Raised when a marketplace plugin reference cannot be resolved."""


class AmbiguousPluginError(PluginResolutionError):
    """Raised when a plugin name exists in multiple registered marketplaces."""

    def __init__(self, plugin_name: str, matching_marketplaces: list[str]) -> None:
        self.plugin_name = plugin_name
        self.matching_marketplaces = matching_marketplaces
        marketplaces = ", ".join(matching_marketplaces)
        super().__init__(
            f"Plugin '{plugin_name}' is ambiguous; found in multiple marketplaces: "
            f"{marketplaces}. Use '{plugin_name}@<marketplace-name>'."
        )


class PluginNotFoundError(PluginResolutionError):
    """Raised when a plugin is absent from the registered marketplaces."""

    def __init__(
        self,
        plugin_name: str,
        marketplace_name: str | None = None,
        fetch_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.plugin_name = plugin_name
        self.marketplace_name = marketplace_name
        self.fetch_errors = fetch_errors or {}
        if marketplace_name is not None:
            message = (
                f"Plugin '{plugin_name}' not found in marketplace '{marketplace_name}'"
            )
        elif fetch_errors:
            details = "; ".join(
                f"{name}: {error}" for name, error in fetch_errors.items()
            )
            message = (
                f"Plugin '{plugin_name}' not found; marketplace fetch failed: {details}"
            )
        else:
            message = f"Plugin '{plugin_name}' not found in any registered marketplace"
        super().__init__(message)


class MarketplaceNotFoundError(PluginResolutionError):
    """Raised when a referenced marketplace is not registered."""

    def __init__(self, marketplace_name: str) -> None:
        self.marketplace_name = marketplace_name
        super().__init__(f"Marketplace '{marketplace_name}' is not registered")


class MarketplaceRegistry:
    """Resolve plugin references against registered marketplaces."""

    def __init__(self, registrations: list[MarketplaceRegistration] | None = None):
        self._registrations: dict[str, MarketplaceRegistration] = {}
        self._cache: dict[str, FetchedMarketplace] = {}
        for registration in registrations or []:
            if registration.name in self._registrations:
                raise ValueError(
                    f"Duplicate marketplace registration: {registration.name}"
                )
            self._registrations[registration.name] = registration

    @property
    def registrations(self) -> dict[str, MarketplaceRegistration]:
        return self._registrations.copy()

    def get_auto_load_registrations(self) -> list[MarketplaceRegistration]:
        return [
            registration
            for registration in self._registrations.values()
            if registration.auto_load
        ]

    def get_marketplace(self, name: str) -> tuple[Marketplace, Path]:
        result = self.get_marketplace_with_resolution(name)
        return result.marketplace, result.path

    def get_marketplace_with_resolution(self, name: str) -> FetchedMarketplace:
        registration = self._registrations.get(name)
        if registration is None:
            raise MarketplaceNotFoundError(name)
        return self._fetch_marketplace(registration)

    def prefetch_all(self) -> None:
        for name, registration in self._registrations.items():
            try:
                self._fetch_marketplace(registration)
            except Exception as exc:
                logger.warning("Failed to prefetch marketplace '%s': %s", name, exc)

    def resolve_plugin(self, plugin_ref: str) -> PluginSource:
        plugin_name, marketplace_name = self._parse_plugin_ref(plugin_ref)
        if marketplace_name is not None:
            return self._resolve_from_marketplace(plugin_name, marketplace_name)
        return self._resolve_from_all(plugin_name)

    def list_plugins(self, marketplace_name: str | None = None) -> list[str]:
        if marketplace_name is not None:
            marketplace, _ = self.get_marketplace(marketplace_name)
            return [plugin.name for plugin in marketplace.plugins]

        plugin_names: list[str] = []
        fetch_errors: dict[str, Exception] = {}
        loaded_count = 0
        for name, registration in self._registrations.items():
            try:
                fetched = self._fetch_marketplace(registration)
            except Exception as exc:
                fetch_errors[name] = exc
                logger.warning("Failed to list plugins from '%s': %s", name, exc)
                continue
            loaded_count += 1
            plugin_names.extend(plugin.name for plugin in fetched.marketplace.plugins)

        if fetch_errors and loaded_count == 0 and self._registrations:
            raise PluginResolutionError(
                "Failed to list plugins; all marketplace fetches failed: "
                + "; ".join(f"{name}: {error}" for name, error in fetch_errors.items())
            )
        return plugin_names

    def _fetch_marketplace(
        self, registration: MarketplaceRegistration
    ) -> FetchedMarketplace:
        if registration.name in self._cache:
            return self._cache[registration.name]

        logger.info(
            "Fetching marketplace '%s' from %s",
            registration.name,
            redact_url_credentials(registration.source),
        )
        path, resolved_ref = fetch_plugin_with_resolution(
            source=registration.source,
            ref=registration.ref,
            repo_path=registration.repo_path,
        )
        fetched = FetchedMarketplace(
            marketplace=Marketplace.load(path),
            path=path,
            resolved_ref=resolved_ref,
        )
        self._cache[registration.name] = fetched
        return fetched

    def _resolve_from_marketplace(
        self, plugin_name: str, marketplace_name: str
    ) -> PluginSource:
        marketplace, _ = self.get_marketplace(marketplace_name)
        plugin = marketplace.get_plugin(plugin_name)
        if plugin is None:
            raise PluginNotFoundError(plugin_name, marketplace_name)
        source, ref, repo_path = marketplace.resolve_plugin_source(plugin)
        return PluginSource(source=source, ref=ref, repo_path=repo_path)

    def _resolve_from_all(self, plugin_name: str) -> PluginSource:
        matches: list[tuple[str, PluginSource]] = []
        fetch_errors: dict[str, Exception] = {}
        searched_count = 0

        for name, registration in self._registrations.items():
            try:
                fetched = self._fetch_marketplace(registration)
            except Exception as exc:
                fetch_errors[name] = exc
                logger.warning(
                    "Failed to search marketplace '%s' for plugin '%s': %s",
                    name,
                    plugin_name,
                    exc,
                )
                continue

            searched_count += 1
            plugin = fetched.marketplace.get_plugin(plugin_name)
            if plugin is None:
                continue
            source, ref, repo_path = fetched.marketplace.resolve_plugin_source(plugin)
            matches.append(
                (name, PluginSource(source=source, ref=ref, repo_path=repo_path))
            )

        if not matches:
            if fetch_errors and searched_count == 0:
                raise PluginNotFoundError(plugin_name, fetch_errors=fetch_errors)
            raise PluginNotFoundError(plugin_name)
        if len(matches) > 1:
            raise AmbiguousPluginError(plugin_name, [name for name, _ in matches])
        return matches[0][1]

    def _parse_plugin_ref(self, plugin_ref: str) -> tuple[str, str | None]:
        if "@" not in plugin_ref:
            if not plugin_ref:
                raise PluginResolutionError("Plugin reference must not be empty")
            return plugin_ref, None

        plugin_name, marketplace_name = plugin_ref.rsplit("@", 1)
        if not plugin_name or not marketplace_name:
            raise PluginResolutionError(
                "Plugin reference must use 'plugin-name@marketplace-name'"
            )
        return plugin_name, marketplace_name
