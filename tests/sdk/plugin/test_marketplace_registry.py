"""Tests for MarketplaceRegistry and MarketplaceRegistration."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from openhands.sdk.plugin import (
    MarketplaceRegistration,
    MarketplaceRegistry,
    PluginSource,
    PluginNotFoundError,
    AmbiguousPluginError,
    MarketplaceNotFoundError,
    Marketplace,
    MarketplaceOwner,
    MarketplacePluginEntry,
)


class TestMarketplaceRegistration:
    """Tests for MarketplaceRegistration model."""

    def test_basic_registration(self):
        """Test creating a basic marketplace registration."""
        reg = MarketplaceRegistration(
            name="test-marketplace",
            source="github:owner/repo",
        )
        assert reg.name == "test-marketplace"
        assert reg.source == "github:owner/repo"
        assert reg.ref is None
        assert reg.repo_path is None
        assert reg.auto_load is None

    def test_registration_with_auto_load(self):
        """Test registration with auto_load='all'."""
        reg = MarketplaceRegistration(
            name="public",
            source="github:OpenHands/skills",
            auto_load="all",
        )
        assert reg.auto_load == "all"

    def test_registration_with_ref(self):
        """Test registration with specific ref."""
        reg = MarketplaceRegistration(
            name="versioned",
            source="github:owner/repo",
            ref="v1.0.0",
        )
        assert reg.ref == "v1.0.0"

    def test_registration_with_repo_path(self):
        """Test registration with repo_path for monorepos."""
        reg = MarketplaceRegistration(
            name="monorepo-marketplace",
            source="github:acme/monorepo",
            repo_path="marketplaces/internal",
        )
        assert reg.repo_path == "marketplaces/internal"

    def test_repo_path_validation_rejects_absolute(self):
        """Test that absolute repo_path is rejected."""
        with pytest.raises(ValueError, match="must be relative"):
            MarketplaceRegistration(
                name="test",
                source="github:owner/repo",
                repo_path="/absolute/path",
            )

    def test_repo_path_validation_rejects_traversal(self):
        """Test that parent directory traversal is rejected."""
        with pytest.raises(ValueError, match="cannot contain '..'"):
            MarketplaceRegistration(
                name="test",
                source="github:owner/repo",
                repo_path="../escape/path",
            )


class TestMarketplaceRegistry:
    """Tests for MarketplaceRegistry."""

    def test_empty_registry(self):
        """Test creating an empty registry."""
        registry = MarketplaceRegistry()
        assert registry.registrations == {}
        assert registry.get_auto_load_registrations() == []

    def test_registry_with_registrations(self):
        """Test creating a registry with registrations."""
        regs = [
            MarketplaceRegistration(name="a", source="github:owner/a", auto_load="all"),
            MarketplaceRegistration(name="b", source="github:owner/b"),
        ]
        registry = MarketplaceRegistry(regs)
        
        assert len(registry.registrations) == 2
        assert "a" in registry.registrations
        assert "b" in registry.registrations

    def test_get_auto_load_registrations(self):
        """Test filtering auto-load registrations."""
        regs = [
            MarketplaceRegistration(name="a", source="github:owner/a", auto_load="all"),
            MarketplaceRegistration(name="b", source="github:owner/b"),
            MarketplaceRegistration(name="c", source="github:owner/c", auto_load="all"),
        ]
        registry = MarketplaceRegistry(regs)
        
        auto_load = registry.get_auto_load_registrations()
        assert len(auto_load) == 2
        assert all(r.auto_load == "all" for r in auto_load)

    def test_marketplace_not_found_error(self):
        """Test error when marketplace not registered."""
        registry = MarketplaceRegistry()
        
        with pytest.raises(MarketplaceNotFoundError) as exc_info:
            registry.get_marketplace("nonexistent")
        
        assert exc_info.value.marketplace_name == "nonexistent"

    def test_parse_plugin_ref_simple(self):
        """Test parsing simple plugin reference."""
        registry = MarketplaceRegistry()
        name, marketplace = registry._parse_plugin_ref("my-plugin")
        assert name == "my-plugin"
        assert marketplace is None

    def test_parse_plugin_ref_with_marketplace(self):
        """Test parsing plugin reference with marketplace."""
        registry = MarketplaceRegistry()
        name, marketplace = registry._parse_plugin_ref("my-plugin@team-tools")
        assert name == "my-plugin"
        assert marketplace == "team-tools"

    def test_parse_plugin_ref_with_at_in_name(self):
        """Test parsing plugin reference with @ in plugin name."""
        registry = MarketplaceRegistry()
        # If plugin name has @, last @ is the delimiter
        name, marketplace = registry._parse_plugin_ref("plugin@1.0@marketplace")
        assert name == "plugin@1.0"
        assert marketplace == "marketplace"


@pytest.fixture
def mock_marketplace_dir(tmp_path):
    """Create a mock marketplace directory structure."""
    # Create .plugin/marketplace.json
    plugin_dir = tmp_path / ".plugin"
    plugin_dir.mkdir()
    
    marketplace_data = {
        "name": "test-marketplace",
        "owner": {"name": "Test Owner"},
        "plugins": [
            {"name": "plugin-a", "source": "./plugins/a", "description": "Plugin A"},
            {"name": "plugin-b", "source": "./plugins/b", "description": "Plugin B"},
        ],
        "skills": [],
    }
    
    (plugin_dir / "marketplace.json").write_text(json.dumps(marketplace_data))
    
    # Create plugin directories
    (tmp_path / "plugins" / "a").mkdir(parents=True)
    (tmp_path / "plugins" / "b").mkdir(parents=True)
    
    return tmp_path


class TestMarketplaceRegistryResolution:
    """Tests for plugin resolution in MarketplaceRegistry."""

    def test_resolve_plugin_from_specific_marketplace(self, mock_marketplace_dir):
        """Test resolving a plugin from a specific marketplace."""
        reg = MarketplaceRegistration(
            name="test",
            source=str(mock_marketplace_dir),
        )
        registry = MarketplaceRegistry([reg])
        
        # Mock fetch to return the local path
        with patch.object(
            registry, '_fetch_marketplace'
        ) as mock_fetch:
            marketplace = Marketplace.load(mock_marketplace_dir)
            mock_fetch.return_value = (marketplace, mock_marketplace_dir)
            
            source = registry.resolve_plugin("plugin-a@test")
            
            assert source.source == str(mock_marketplace_dir / "plugins" / "a")

    def test_resolve_plugin_not_found_in_marketplace(self, mock_marketplace_dir):
        """Test error when plugin not found in specified marketplace."""
        reg = MarketplaceRegistration(
            name="test",
            source=str(mock_marketplace_dir),
        )
        registry = MarketplaceRegistry([reg])
        
        with patch.object(
            registry, '_fetch_marketplace'
        ) as mock_fetch:
            marketplace = Marketplace.load(mock_marketplace_dir)
            mock_fetch.return_value = (marketplace, mock_marketplace_dir)
            
            with pytest.raises(PluginNotFoundError) as exc_info:
                registry.resolve_plugin("nonexistent@test")
            
            assert exc_info.value.plugin_name == "nonexistent"
            assert exc_info.value.marketplace_name == "test"

    def test_resolve_plugin_marketplace_not_registered(self):
        """Test error when referenced marketplace is not registered."""
        registry = MarketplaceRegistry()
        
        with pytest.raises(MarketplaceNotFoundError) as exc_info:
            registry.resolve_plugin("plugin@unknown")
        
        assert exc_info.value.marketplace_name == "unknown"

    def test_resolve_plugin_search_all_marketplaces(self, mock_marketplace_dir):
        """Test resolving a plugin by searching all marketplaces."""
        reg = MarketplaceRegistration(
            name="test",
            source=str(mock_marketplace_dir),
        )
        registry = MarketplaceRegistry([reg])
        
        with patch.object(
            registry, '_fetch_marketplace'
        ) as mock_fetch:
            marketplace = Marketplace.load(mock_marketplace_dir)
            mock_fetch.return_value = (marketplace, mock_marketplace_dir)
            
            source = registry.resolve_plugin("plugin-a")
            
            assert source.source == str(mock_marketplace_dir / "plugins" / "a")

    def test_resolve_plugin_not_found_anywhere(self, mock_marketplace_dir):
        """Test error when plugin not found in any marketplace."""
        reg = MarketplaceRegistration(
            name="test",
            source=str(mock_marketplace_dir),
        )
        registry = MarketplaceRegistry([reg])
        
        with patch.object(
            registry, '_fetch_marketplace'
        ) as mock_fetch:
            marketplace = Marketplace.load(mock_marketplace_dir)
            mock_fetch.return_value = (marketplace, mock_marketplace_dir)
            
            with pytest.raises(PluginNotFoundError) as exc_info:
                registry.resolve_plugin("nonexistent")
            
            assert exc_info.value.plugin_name == "nonexistent"
            assert exc_info.value.marketplace_name is None

    def test_resolve_plugin_ambiguous(self, tmp_path):
        """Test error when plugin found in multiple marketplaces."""
        # Create two marketplace directories with same plugin name
        for name in ["marketplace1", "marketplace2"]:
            mp_dir = tmp_path / name
            mp_dir.mkdir()
            plugin_dir = mp_dir / ".plugin"
            plugin_dir.mkdir()
            
            marketplace_data = {
                "name": name,
                "owner": {"name": "Owner"},
                "plugins": [
                    {"name": "common-plugin", "source": "./plugins/common"},
                ],
            }
            (plugin_dir / "marketplace.json").write_text(json.dumps(marketplace_data))
            (mp_dir / "plugins" / "common").mkdir(parents=True)
        
        regs = [
            MarketplaceRegistration(name="mp1", source=str(tmp_path / "marketplace1")),
            MarketplaceRegistration(name="mp2", source=str(tmp_path / "marketplace2")),
        ]
        registry = MarketplaceRegistry(regs)
        
        def mock_fetch(reg):
            mp_path = tmp_path / ("marketplace1" if reg.name == "mp1" else "marketplace2")
            marketplace = Marketplace.load(mp_path)
            return (marketplace, mp_path)
        
        with patch.object(registry, '_fetch_marketplace', side_effect=mock_fetch):
            with pytest.raises(AmbiguousPluginError) as exc_info:
                registry.resolve_plugin("common-plugin")
            
            assert exc_info.value.plugin_name == "common-plugin"
            assert set(exc_info.value.matching_marketplaces) == {"mp1", "mp2"}

    def test_list_plugins_from_marketplace(self, mock_marketplace_dir):
        """Test listing plugins from a specific marketplace."""
        reg = MarketplaceRegistration(
            name="test",
            source=str(mock_marketplace_dir),
        )
        registry = MarketplaceRegistry([reg])
        
        with patch.object(
            registry, '_fetch_marketplace'
        ) as mock_fetch:
            marketplace = Marketplace.load(mock_marketplace_dir)
            mock_fetch.return_value = (marketplace, mock_marketplace_dir)
            
            plugins = registry.list_plugins("test")
            
            assert set(plugins) == {"plugin-a", "plugin-b"}

    def test_list_plugins_from_all(self, mock_marketplace_dir):
        """Test listing plugins from all marketplaces."""
        reg = MarketplaceRegistration(
            name="test",
            source=str(mock_marketplace_dir),
        )
        registry = MarketplaceRegistry([reg])
        
        with patch.object(
            registry, '_fetch_marketplace'
        ) as mock_fetch:
            marketplace = Marketplace.load(mock_marketplace_dir)
            mock_fetch.return_value = (marketplace, mock_marketplace_dir)
            
            plugins = registry.list_plugins()
            
            assert set(plugins) == {"plugin-a", "plugin-b"}
