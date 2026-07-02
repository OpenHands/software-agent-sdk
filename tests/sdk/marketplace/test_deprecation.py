"""Tests for marketplace module (canonical location) and removed shims."""

import pytest

from openhands.sdk.marketplace import (
    MARKETPLACE_MANIFEST_DIRS,
    MARKETPLACE_MANIFEST_FILE,
    Marketplace,
    MarketplaceEntry,
    MarketplaceMetadata,
    MarketplaceOwner,
    MarketplacePluginEntry,
    MarketplacePluginSource,
)


def test_new_import_location_has_all_exports():
    """Test that all marketplace classes are available from the new location."""
    # Constants
    assert MARKETPLACE_MANIFEST_DIRS == [".plugin", ".claude-plugin"]
    assert MARKETPLACE_MANIFEST_FILE == "marketplace.json"

    # Classes
    assert Marketplace is not None
    assert MarketplaceEntry is not None
    assert MarketplaceOwner is not None
    assert MarketplacePluginEntry is not None
    assert MarketplacePluginSource is not None
    assert MarketplaceMetadata is not None


def test_removed_import_from_plugin_raises():
    """Test that importing marketplace classes from plugin raises AttributeError."""
    from openhands.sdk import plugin

    with pytest.raises(AttributeError):
        plugin.Marketplace  # type: ignore[attr-defined]  # noqa: B018


def test_removed_import_from_plugin_types_raises():
    """Test that importing marketplace classes from plugin.types raises."""
    from openhands.sdk.plugin import types

    with pytest.raises(AttributeError):
        types.MarketplaceOwner  # type: ignore[attr-defined]  # noqa: B018


def test_marketplace_functionality_preserved():
    """Test that Marketplace class functionality works from canonical location."""
    owner = MarketplaceOwner(name="Test Team")
    assert owner.name == "Test Team"

    source = MarketplacePluginSource(source="github", repo="owner/repo")
    assert source.repo == "owner/repo"

    entry = MarketplaceEntry(name="test-skill", source="./skills/test")
    assert entry.name == "test-skill"

    plugin_entry = MarketplacePluginEntry(
        name="test-plugin",
        source="./plugins/test",
        description="A test plugin",
    )
    assert plugin_entry.description == "A test plugin"

    metadata = MarketplaceMetadata(version="1.0.0")
    assert metadata.version == "1.0.0"
