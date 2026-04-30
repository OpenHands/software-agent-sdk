from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .acp_providers import (
    ACP_PROVIDERS,
    ACPProviderInfo,
    detect_acp_provider_by_agent_name,
    get_acp_provider,
)
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
        AgentSettingsBase,
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
    "AgentSettingsBase",
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

__all__ = [
    "ACP_PROVIDERS",
    "ACPProviderInfo",
    "AGENT_SETTINGS_SCHEMA_VERSION",
    "CONVERSATION_SETTINGS_SCHEMA_VERSION",
    "ACPAgentSettings",
    "AgentKind",
    "AgentSettings",
    "AgentSettingsBase",
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
    "detect_acp_provider_by_agent_name",
    "export_agent_settings_schema",
    "export_settings_schema",
    "field_meta",
    "get_acp_provider",
    "validate_agent_settings",
]


def __getattr__(name: str) -> Any:
    if name == "LLMAgentSettings":
        from openhands.sdk.utils.deprecation import warn_deprecated

        warn_deprecated(
            f"Importing {name!r} from openhands.sdk.settings",
            deprecated_in="1.19.0",
            removed_in="1.22.0",
            details=(
                "Use ``OpenHandsAgentSettings`` directly. "
                "``LLMAgentSettings`` was renamed in v1.19.0."
            ),
            stacklevel=3,
        )
        from . import model

        return getattr(model, name)
    if name in _MODEL_EXPORTS:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
