"""Backward-compatible re-exports for SDK settings metadata helpers."""

from openhands.sdk.settings.metadata import (
    SETTINGS_METADATA_KEY,
    SETTINGS_SECTION_METADATA_KEY,
    SettingProminence,
    SettingsFieldMetadata,
    SettingsSectionMetadata,
    field_meta,
)


__all__ = [
    "SETTINGS_METADATA_KEY",
    "SETTINGS_SECTION_METADATA_KEY",
    "SettingProminence",
    "SettingsFieldMetadata",
    "SettingsSectionMetadata",
    "field_meta",
]
