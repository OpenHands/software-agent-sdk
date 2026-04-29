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

# Names that have been deprecated and emit a warning when imported.
# Importing from here triggers a DeprecationWarning; use the canonical
# replacement listed in ``details``.
_DEPRECATED_MODEL_EXPORTS: dict[str, dict[str, str]] = {
    "LLMAgentSettings": {
        "deprecated_in": "1.19.0",
        "removed_in": "1.22.0",
        "details": (
            "Use ``OpenHandsAgentSettings`` directly. "
            "``LLMAgentSettings`` was renamed in v1.19.0."
        ),
    },
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
    if name in _DEPRECATED_MODEL_EXPORTS:
        from openhands.sdk.utils.deprecation import warn_deprecated

        info = _DEPRECATED_MODEL_EXPORTS[name]
        warn_deprecated(
            f"Importing {name!r} from openhands.sdk.settings",
            deprecated_in=info["deprecated_in"],
            removed_in=info["removed_in"],
            details=info["details"],
            stacklevel=3,
        )
        from . import model

        return getattr(model, name)
    if name in _MODEL_EXPORTS:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
