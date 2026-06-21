"""Tests for marketplace registration and plugin resolution."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openhands.sdk.marketplace import (
    AmbiguousPluginError,
    MarketplaceNotFoundError,
    MarketplaceRegistration,
    MarketplaceRegistry,
    PluginNotFoundError,
    PluginResolutionError,
)


def _create_marketplace(
    marketplace_dir: Path,
    name: str = "test-marketplace",
    plugins: list[dict] | None = None,
) -> Path:
    manifest_dir = marketplace_dir / ".plugin"
    manifest_dir.mkdir(parents=True)
    manifest = {
        "name": name,
        "owner": {"name": "Test Team"},
        "plugins": plugins or [],
    }
    (manifest_dir / "marketplace.json").write_text(json.dumps(manifest))
    return marketplace_dir


def test_marketplace_registration_accepts_all_fields() -> None:
    registration = MarketplaceRegistration(
        name="team",
        source="github:example/marketplaces",
        ref="v1.0.0",
        repo_path="marketplaces/team",
        auto_load="all",
    )

    assert registration.name == "team"
    assert registration.source == "github:example/marketplaces"
    assert registration.ref == "v1.0.0"
    assert registration.repo_path == "marketplaces/team"
    assert registration.auto_load == "all"


@pytest.mark.parametrize(
    "repo_path",
    ["", "/absolute", "C:/absolute", "C:\\absolute", "safe/../escape", "safe\\path"],
)
def test_marketplace_registration_rejects_unsafe_repo_paths(repo_path: str) -> None:
    with pytest.raises(ValidationError):
        MarketplaceRegistration(
            name="team",
            source="github:example/marketplaces",
            repo_path=repo_path,
        )


def test_registry_rejects_duplicate_registration_names(tmp_path: Path) -> None:
    source = str(_create_marketplace(tmp_path / "marketplace"))

    with pytest.raises(ValueError, match="Duplicate marketplace registration"):
        MarketplaceRegistry(
            [
                MarketplaceRegistration(name="team", source=source),
                MarketplaceRegistration(name="team", source=source),
            ]
        )


def test_get_auto_load_registrations(tmp_path: Path) -> None:
    source = str(_create_marketplace(tmp_path / "marketplace"))
    auto_registration = MarketplaceRegistration(
        name="auto",
        source=source,
        auto_load="all",
    )
    manual_registration = MarketplaceRegistration(name="manual", source=source)

    registry = MarketplaceRegistry([auto_registration, manual_registration])

    assert registry.get_auto_load_registrations() == [auto_registration]


def test_get_marketplace_fetches_and_caches_manifest(tmp_path: Path) -> None:
    marketplace_dir = _create_marketplace(
        tmp_path / "marketplace",
        plugins=[{"name": "first", "source": "./plugins/first"}],
    )
    registry = MarketplaceRegistry(
        [MarketplaceRegistration(name="team", source=str(marketplace_dir))]
    )

    marketplace, path = registry.get_marketplace("team")
    assert marketplace.name == "test-marketplace"
    assert path == marketplace_dir

    manifest_path = marketplace_dir / ".plugin" / "marketplace.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "changed",
                "owner": {"name": "Test Team"},
                "plugins": [{"name": "second", "source": "./plugins/second"}],
            }
        )
    )

    cached_marketplace, cached_path = registry.get_marketplace("team")
    assert cached_marketplace is marketplace
    assert cached_path == marketplace_dir
    assert registry.list_plugins("team") == ["first"]


def test_get_marketplace_unknown_name_raises() -> None:
    registry = MarketplaceRegistry()

    with pytest.raises(MarketplaceNotFoundError, match="not registered"):
        registry.get_marketplace("missing")


def test_resolve_plugin_from_explicit_marketplace(tmp_path: Path) -> None:
    marketplace_dir = _create_marketplace(
        tmp_path / "marketplace",
        plugins=[{"name": "formatter", "source": "./plugins/formatter"}],
    )
    registry = MarketplaceRegistry(
        [MarketplaceRegistration(name="team", source=str(marketplace_dir))]
    )

    plugin_source = registry.resolve_plugin("formatter@team")

    assert Path(plugin_source.source) == marketplace_dir / "plugins" / "formatter"
    assert plugin_source.ref is None
    assert plugin_source.repo_path is None


def test_resolve_plugin_searches_all_marketplaces(tmp_path: Path) -> None:
    first = _create_marketplace(
        tmp_path / "first",
        plugins=[{"name": "formatter", "source": "./plugins/formatter"}],
    )
    second = _create_marketplace(
        tmp_path / "second",
        plugins=[{"name": "linter", "source": "./plugins/linter"}],
    )
    registry = MarketplaceRegistry(
        [
            MarketplaceRegistration(name="first", source=str(first)),
            MarketplaceRegistration(name="second", source=str(second)),
        ]
    )

    plugin_source = registry.resolve_plugin("linter")

    assert Path(plugin_source.source) == second / "plugins" / "linter"


def test_resolve_plugin_with_complex_source_preserves_ref_and_path(
    tmp_path: Path,
) -> None:
    marketplace_dir = _create_marketplace(
        tmp_path / "marketplace",
        plugins=[
            {
                "name": "remote",
                "source": {
                    "source": "github",
                    "repo": "example/plugins",
                    "ref": "release",
                    "path": "plugins/remote",
                },
            }
        ],
    )
    registry = MarketplaceRegistry(
        [MarketplaceRegistration(name="team", source=str(marketplace_dir))]
    )

    plugin_source = registry.resolve_plugin("remote@team")

    assert plugin_source.source == "github:example/plugins"
    assert plugin_source.ref == "release"
    assert plugin_source.repo_path == "plugins/remote"


def test_resolve_plugin_raises_for_unknown_plugin_in_explicit_marketplace(
    tmp_path: Path,
) -> None:
    marketplace_dir = _create_marketplace(tmp_path / "marketplace")
    registry = MarketplaceRegistry(
        [MarketplaceRegistration(name="team", source=str(marketplace_dir))]
    )

    with pytest.raises(PluginNotFoundError, match="not found in marketplace 'team'"):
        registry.resolve_plugin("missing@team")


def test_resolve_plugin_raises_for_unknown_plugin_across_all_marketplaces(
    tmp_path: Path,
) -> None:
    marketplace_dir = _create_marketplace(tmp_path / "marketplace")
    registry = MarketplaceRegistry(
        [MarketplaceRegistration(name="team", source=str(marketplace_dir))]
    )

    with pytest.raises(PluginNotFoundError, match="not found in any registered"):
        registry.resolve_plugin("missing")


def test_resolve_plugin_raises_for_ambiguous_plugin_name(tmp_path: Path) -> None:
    first = _create_marketplace(
        tmp_path / "first",
        plugins=[{"name": "shared", "source": "./plugins/first"}],
    )
    second = _create_marketplace(
        tmp_path / "second",
        plugins=[{"name": "shared", "source": "./plugins/second"}],
    )
    registry = MarketplaceRegistry(
        [
            MarketplaceRegistration(name="first", source=str(first)),
            MarketplaceRegistration(name="second", source=str(second)),
        ]
    )

    with pytest.raises(AmbiguousPluginError) as exc_info:
        registry.resolve_plugin("shared")

    assert exc_info.value.plugin_name == "shared"
    assert exc_info.value.matching_marketplaces == ["first", "second"]


def test_resolve_plugin_reports_fetch_errors_when_all_marketplaces_fail(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    registry = MarketplaceRegistry(
        [MarketplaceRegistration(name="broken", source=str(missing))]
    )

    with pytest.raises(PluginNotFoundError) as exc_info:
        registry.resolve_plugin("anything")

    assert "broken" in exc_info.value.fetch_errors


def test_list_plugins_from_specific_marketplace(tmp_path: Path) -> None:
    marketplace_dir = _create_marketplace(
        tmp_path / "marketplace",
        plugins=[
            {"name": "formatter", "source": "./plugins/formatter"},
            {"name": "linter", "source": "./plugins/linter"},
        ],
    )
    registry = MarketplaceRegistry(
        [MarketplaceRegistration(name="team", source=str(marketplace_dir))]
    )

    assert registry.list_plugins("team") == ["formatter", "linter"]


def test_list_plugins_from_all_marketplaces(tmp_path: Path) -> None:
    first = _create_marketplace(
        tmp_path / "first",
        plugins=[{"name": "formatter", "source": "./plugins/formatter"}],
    )
    second = _create_marketplace(
        tmp_path / "second",
        plugins=[{"name": "linter", "source": "./plugins/linter"}],
    )
    registry = MarketplaceRegistry(
        [
            MarketplaceRegistration(name="first", source=str(first)),
            MarketplaceRegistration(name="second", source=str(second)),
        ]
    )

    assert registry.list_plugins() == ["formatter", "linter"]


def test_list_plugins_raises_when_all_marketplaces_fail(tmp_path: Path) -> None:
    registry = MarketplaceRegistry(
        [MarketplaceRegistration(name="broken", source=str(tmp_path / "missing"))]
    )

    with pytest.raises(PluginResolutionError, match="all marketplace fetches failed"):
        registry.list_plugins()
