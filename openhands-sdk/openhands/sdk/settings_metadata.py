from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


SETTINGS_METADATA_KEY = "openhands_settings"
SETTINGS_SECTION_METADATA_KEY = "openhands_settings_section"


class SettingProminence(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class SettingsSectionMetadata(BaseModel):
    key: str
    label: str | None = None


class SettingsFieldMetadata(BaseModel):
    label: str | None = None
    prominence: SettingProminence = SettingProminence.MAJOR
    depends_on: tuple[str, ...] = ()


def field_meta(
    prominence: SettingProminence = SettingProminence.MAJOR,
    *,
    label: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> dict[str, dict[str, object]]:
    """Build a ``json_schema_extra`` dict for a Pydantic ``Field``.

    Example::

        model: str = Field(..., json_schema_extra=field_meta(SettingProminence.CRITICAL))
    """
    return {
        SETTINGS_METADATA_KEY: SettingsFieldMetadata(
            label=label,
            prominence=prominence,
            depends_on=depends_on,
        ).model_dump()
    }
