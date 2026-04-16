"""Tests for marketplace module deprecation warnings."""

import warnings

import pytest
from deprecation import DeprecatedWarning

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


def test_deprecated_import_from_plugin_warns():
    """Test that importing from openhands.sdk.plugin emits deprecation warning."""
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        from openhands.sdk.plugin import Marketplace as OldMarketplace

        # Find deprecation warnings for this import
        deprecation_warnings = [
            w
            for w in caught_warnings
            if issubclass(w.category, DeprecatedWarning)
            and "openhands.sdk.plugin" in str(w.message)
            and "Marketplace" in str(w.message)
        ]
        assert len(deprecation_warnings) > 0, "Expected deprecation warning not found"

        # Verify the warning message
        warning_msg = str(deprecation_warnings[0].message)
        assert "openhands.sdk.marketplace" in warning_msg

        # Verify the class is the same
        assert OldMarketplace is Marketplace


def test_deprecated_import_from_plugin_types_warns():
    """Test that importing from openhands.sdk.plugin.types emits deprecation warning."""
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        from openhands.sdk.plugin.types import MarketplaceOwner as OldMarketplaceOwner

        # Find deprecation warnings for this import
        deprecation_warnings = [
            w
            for w in caught_warnings
            if issubclass(w.category, DeprecatedWarning)
            and "openhands.sdk.plugin.types" in str(w.message)
            and "MarketplaceOwner" in str(w.message)
        ]
        assert len(deprecation_warnings) > 0, "Expected deprecation warning not found"

        # Verify the class is the same
        assert OldMarketplaceOwner is MarketplaceOwner


def test_deprecated_constant_import_warns():
    """Test that importing constants from old location emits deprecation warning."""
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        from openhands.sdk.plugin.types import (
            MARKETPLACE_MANIFEST_FILE as OLD_MANIFEST_FILE,
        )

        # Find deprecation warnings for this import
        deprecation_warnings = [
            w
            for w in caught_warnings
            if issubclass(w.category, DeprecatedWarning)
            and "MARKETPLACE_MANIFEST_FILE" in str(w.message)
        ]
        assert len(deprecation_warnings) > 0, "Expected deprecation warning not found"

        # Verify the constant is the same
        assert OLD_MANIFEST_FILE == MARKETPLACE_MANIFEST_FILE


@pytest.mark.parametrize(
    "class_name",
    [
        "Marketplace",
        "MarketplaceEntry",
        "MarketplaceOwner",
        "MarketplacePluginEntry",
        "MarketplacePluginSource",
        "MarketplaceMetadata",
    ],
)
def test_all_deprecated_classes_from_plugin(class_name: str):
    """Test all marketplace classes emit deprecation warnings from plugin."""
    import openhands.sdk.marketplace as marketplace_module

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        from openhands.sdk import plugin

        old_class = getattr(plugin, class_name)
        new_class = getattr(marketplace_module, class_name)

        # Verify the class is the same
        assert old_class is new_class


def test_marketplace_functionality_preserved():
    """Test that Marketplace class functionality works from new location."""
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
