from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel
from pydantic.config import JsonDict


SETTINGS_METADATA_KEY = "openhands_settings"
SETTINGS_SECTION_METADATA_KEY = "openhands_settings_section"


class SettingProminence(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class SettingsSectionMetadata(BaseModel):
    key: str
    label: str | None = None
    variant: str | None = None


class SettingsFieldMetadata(BaseModel):
    label: str | None = None
    prominence: SettingProminence = SettingProminence.MINOR
    depends_on: tuple[str, ...] = ()
    variant: str | None = None
    """When set, the field only applies to the named ``AgentSettings``
    variant (``"llm"`` or ``"acp"``). Fields with ``variant=None`` are
    shown regardless of the active ``agent_kind``."""


def field_meta(
    prominence: SettingProminence = SettingProminence.MINOR,
    *,
    label: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> JsonDict:
    """Build a ``json_schema_extra`` dict for a Pydantic ``Field``.

    Example::

        model: str = Field(
            ..., json_schema_extra=field_meta(SettingProminence.CRITICAL)
        )
    """
    metadata: JsonDict = SettingsFieldMetadata(
        label=label,
        prominence=prominence,
        depends_on=depends_on,
    ).model_dump(mode="json")
    return {SETTINGS_METADATA_KEY: metadata}
