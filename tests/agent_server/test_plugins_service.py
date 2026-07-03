"""Tests for the plugins-only marketplace catalog (plugins_service / plugins_router).

The service runs the real filter + resolve logic against a controlled
marketplace; only the network-bound ``update_skills_repository`` is stubbed.
"""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server import plugins_service
from openhands.agent_server.plugins_router import plugins_router
from openhands.agent_server.plugins_service import (
    MarketplacePluginInfo,
    service_get_plugins_marketplace_catalog,
)
from openhands.sdk.plugin import install_plugin


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    """Reset the module-level TTL cache so tests don't leak entries to each other."""
    plugins_service._plugin_catalog_cache = None
    yield
    plugins_service._plugin_catalog_cache = None


def _write_marketplace(repo_dir: Path, plugins: list[dict]) -> Path:
    """Write the marketplace JSON and return the repo dir."""
    mp_file = repo_dir / "marketplaces" / "default.json"
    mp_file.parent.mkdir(parents=True, exist_ok=True)
    mp_file.write_text(
        json.dumps({"name": "test-mp", "owner": {"name": "Test"}, "plugins": plugins})
    )
    return repo_dir


def _write_manifest_marketplace(repo_dir: Path, plugins: list[dict]) -> Path:
    """Write the catalog as a ``.plugin/marketplace.json`` manifest.

    Mirrors the real OpenHands/extensions layout, where the catalog is
    discovered via ``Marketplace.load`` (``.plugin/marketplace.json``) and
    ``marketplaces/default.json`` is absent.
    """
    mp_file = repo_dir / ".plugin" / "marketplace.json"
    mp_file.parent.mkdir(parents=True, exist_ok=True)
    mp_file.write_text(
        json.dumps({"name": "test-mp", "owner": {"name": "Test"}, "plugins": plugins})
    )
    return repo_dir


def _make_installable_plugin(plugin_dir: Path, name: str) -> Path:
    """Create a minimal installable plugin directory."""
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "description": name})
    )
    return plugin_dir


def test_catalog_returns_only_true_plugins(tmp_path: Path, monkeypatch):
    # Arrange: a marketplace mixing a true plugin (./plugins/) and a skill
    # (./skills/), as the real OpenHands marketplace does.
    repo = _write_marketplace(
        tmp_path / "ext",
        [
            {"name": "local-plugin", "source": "./plugins/local-plugin"},
            {"name": "a-skill", "source": "./skills/a-skill"},
        ],
    )
    monkeypatch.setattr(
        plugins_service, "update_skills_repository", lambda *a, **k: repo
    )
    installed = tmp_path / "installed"
    installed.mkdir()

    # Act
    catalog = service_get_plugins_marketplace_catalog(installed_dir=installed)

    # Assert: only the ./plugins/ entry, resolved to local PluginSource coords.
    assert [p.name for p in catalog] == ["local-plugin"]
    assert catalog[0].source.endswith("plugins/local-plugin")
    assert catalog[0].ref is None
    assert catalog[0].repo_path is None


def test_catalog_loads_from_plugin_manifest_layout(tmp_path: Path, monkeypatch):
    # Regression: the real extensions repo ships the catalog as
    # .plugin/marketplace.json (discovered by Marketplace.load) and has NO
    # marketplaces/default.json. The service must not early-return on the
    # missing default.json and blank the catalog.
    repo = _write_manifest_marketplace(
        tmp_path / "ext",
        [
            {"name": "city-weather", "source": "./plugins/city-weather"},
            {"name": "a-skill", "source": "./skills/a-skill"},
        ],
    )
    assert not (repo / "marketplaces" / "default.json").exists()
    monkeypatch.setattr(
        plugins_service, "update_skills_repository", lambda *a, **k: repo
    )
    installed = tmp_path / "installed"
    installed.mkdir()

    # Act
    catalog = service_get_plugins_marketplace_catalog(installed_dir=installed)

    # Assert: the true plugin is returned (not an empty catalog), skill excluded.
    assert [p.name for p in catalog] == ["city-weather"]
    assert catalog[0].source.endswith("plugins/city-weather")


def test_catalog_resolves_structured_source_and_excludes_structured_skills(
    tmp_path: Path, monkeypatch
):
    # Arrange: structured (github) sources — one under plugins/, one under skills/.
    repo = _write_marketplace(
        tmp_path / "ext",
        [
            {
                "name": "gh-plugin",
                "source": {
                    "source": "github",
                    "repo": "owner/repo",
                    "ref": "v1.0.0",
                    "path": "plugins/gh-plugin",
                },
            },
            {
                "name": "gh-skill",
                "source": {
                    "source": "github",
                    "repo": "owner/repo",
                    "path": "skills/gh-skill",
                },
            },
        ],
    )
    monkeypatch.setattr(
        plugins_service, "update_skills_repository", lambda *a, **k: repo
    )
    installed = tmp_path / "installed"
    installed.mkdir()

    # Act
    catalog = service_get_plugins_marketplace_catalog(installed_dir=installed)

    # Assert: the structured skill is excluded; the plugin keeps its coordinates.
    assert [p.name for p in catalog] == ["gh-plugin"]
    assert catalog[0].source == "github:owner/repo"
    assert catalog[0].ref == "v1.0.0"
    assert catalog[0].repo_path == "plugins/gh-plugin"


def test_catalog_marks_installed_plugins(tmp_path: Path, monkeypatch):
    # Arrange: two catalog plugins; one is installed locally.
    repo = _write_marketplace(
        tmp_path / "ext",
        [
            {"name": "installed-plugin", "source": "./plugins/installed-plugin"},
            {"name": "available-plugin", "source": "./plugins/available-plugin"},
        ],
    )
    monkeypatch.setattr(
        plugins_service, "update_skills_repository", lambda *a, **k: repo
    )
    store = tmp_path / "installed"
    store.mkdir()
    install_plugin(
        str(_make_installable_plugin(tmp_path / "src", "installed-plugin")),
        installed_dir=store,
    )

    # Act
    catalog = service_get_plugins_marketplace_catalog(installed_dir=store)

    # Assert: install state reflects the local install store.
    installed_by_name = {p.name: p.installed for p in catalog}
    assert installed_by_name["installed-plugin"] is True
    assert installed_by_name["available-plugin"] is False


class TestPluginsMarketplaceRoute:
    def test_marketplace_route_returns_plugins_payload(self, monkeypatch):
        # Arrange
        sample = [
            MarketplacePluginInfo(
                name="p",
                description="d",
                source="github:o/r",
                ref="v1",
                repo_path="plugins/p",
                installed=True,
            )
        ]
        monkeypatch.setattr(
            "openhands.agent_server.plugins_router."
            "service_get_plugins_marketplace_catalog",
            lambda: sample,
        )
        app = FastAPI()
        app.include_router(plugins_router)
        client = TestClient(app)

        # Act
        resp = client.get("/plugins/marketplace")

        # Assert: response is wrapped as {"plugins": [...]} with the full coords.
        assert resp.status_code == 200
        assert resp.json() == {
            "plugins": [
                {
                    "name": "p",
                    "description": "d",
                    "source": "github:o/r",
                    "ref": "v1",
                    "repo_path": "plugins/p",
                    "installed": True,
                }
            ]
        }
