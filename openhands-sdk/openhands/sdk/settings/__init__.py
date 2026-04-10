from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .metadata import (
    SETTINGS_METADATA_KEY,
    SETTINGS_SECTION_METADATA_KEY,
    SettingProminence,
    SettingsFieldMetadata,
    SettingsSectionMetadata,
    field_meta,
)


if TYPE_CHECKING:
    from .model import (
        AgentSettings,
        CondenserSettings,
        SettingsChoice,
        SettingsFieldSchema,
        SettingsSchema,
        SettingsSectionSchema,
        VerificationSettings,
        export_settings_schema,
    )

_MODEL_EXPORTS = {
    "AgentSettings",
    "CondenserSettings",
    "SettingsChoice",
    "SettingsFieldSchema",
    "SettingsSchema",
    "SettingsSectionSchema",
    "VerificationSettings",
    "export_settings_schema",
}

__all__ = [
    "AgentSettings",
    "CondenserSettings",
    "SETTINGS_METADATA_KEY",
    "SETTINGS_SECTION_METADATA_KEY",
    "SettingProminence",
    "SettingsChoice",
    "SettingsFieldMetadata",
    "SettingsFieldSchema",
    "SettingsSchema",
    "SettingsSectionMetadata",
    "SettingsSectionSchema",
    "VerificationSettings",
    "export_settings_schema",
    "field_meta",
]


def __getattr__(name: str) -> Any:
    if name in _MODEL_EXPORTS:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
