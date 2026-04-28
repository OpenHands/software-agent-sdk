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
        AGENT_SETTINGS_SCHEMA_VERSION,
        CONVERSATION_SETTINGS_SCHEMA_VERSION,
        ACPAgentSettings,
        AgentKind,
        AgentSettings,
        AgentSettingsConfig,
        CondenserSettings,
        ConversationSettings,
        LLMAgentSettings,
        OpenHandsAgentSettings,
        SettingsChoice,
        SettingsFieldSchema,
        SettingsSchema,
        SettingsSectionSchema,
        VerificationSettings,
        create_agent_from_settings,
        default_agent_settings,
        export_agent_settings_schema,
        export_settings_schema,
        validate_agent_settings,
    )

_MODEL_EXPORTS = {
    "AGENT_SETTINGS_SCHEMA_VERSION",
    "CONVERSATION_SETTINGS_SCHEMA_VERSION",
    "ACPAgentSettings",
    "AgentKind",
    "AgentSettings",
    "AgentSettingsConfig",
    "CondenserSettings",
    "ConversationSettings",
    "LLMAgentSettings",
    "OpenHandsAgentSettings",
    "SettingsChoice",
    "SettingsFieldSchema",
    "SettingsSchema",
    "SettingsSectionSchema",
    "VerificationSettings",
    "create_agent_from_settings",
    "default_agent_settings",
    "export_agent_settings_schema",
    "export_settings_schema",
    "validate_agent_settings",
}

__all__ = [
    "AGENT_SETTINGS_SCHEMA_VERSION",
    "CONVERSATION_SETTINGS_SCHEMA_VERSION",
    "ACPAgentSettings",
    "AgentKind",
    "AgentSettings",
    "AgentSettingsConfig",
    "CondenserSettings",
    "ConversationSettings",
    "LLMAgentSettings",
    "OpenHandsAgentSettings",
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
    "create_agent_from_settings",
    "default_agent_settings",
    "export_agent_settings_schema",
    "export_settings_schema",
    "field_meta",
    "validate_agent_settings",
]


def __getattr__(name: str) -> Any:
    if name in _MODEL_EXPORTS:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
