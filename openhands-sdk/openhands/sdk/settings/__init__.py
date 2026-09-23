from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .acp_providers import (
    ACP_PROVIDERS,
    ACPFileSecretSpec,
    ACPModelOption,
    ACPProviderInfo,
    build_session_model_meta,
    default_acp_file_secrets,
    detect_acp_provider_by_agent_name,
    get_acp_provider,
)
from .api_models import (
    SecretCreateRequest,
    SecretItemResponse,
    SecretsListResponse,
    SettingsResponse,
    SettingsUpdateRequest,
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
        AgentSettingsBase,
        AgentSettingsConfig,
        CondenserSettings,
        CondenserSettingsConfig,
        ConversationSettings,
        LLMSummarizingCondenserSettings,
        NoOpCondenserSettings,
        OpenHandsAgentSettings,
        SettingsChoice,
        SettingsFieldSchema,
        SettingsSchema,
        SettingsSectionSchema,
        VerificationSettings,
        apply_agent_settings_diff,
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
    "AgentSettingsBase",
    "AgentSettingsConfig",
    "CondenserSettings",
    "CondenserSettingsConfig",
    "ConversationSettings",
    "LLMSummarizingCondenserSettings",
    "NoOpCondenserSettings",
    "OpenHandsAgentSettings",
    "SettingsChoice",
    "SettingsFieldSchema",
    "SettingsSchema",
    "SettingsSectionSchema",
    "VerificationSettings",
    "apply_agent_settings_diff",
    "create_agent_from_settings",
    "default_agent_settings",
    "export_agent_settings_schema",
    "export_settings_schema",
    "validate_agent_settings",
}

__all__ = [
    "ACP_PROVIDERS",
    "ACPFileSecretSpec",
    "ACPModelOption",
    "ACPProviderInfo",
    "build_session_model_meta",
    "default_acp_file_secrets",
    "AGENT_SETTINGS_SCHEMA_VERSION",
    "CONVERSATION_SETTINGS_SCHEMA_VERSION",
    "ACPAgentSettings",
    "AgentKind",
    "AgentSettingsBase",
    "AgentSettingsConfig",
    "CondenserSettings",
    "CondenserSettingsConfig",
    "ConversationSettings",
    "LLMSummarizingCondenserSettings",
    "NoOpCondenserSettings",
    "OpenHandsAgentSettings",
    "SETTINGS_METADATA_KEY",
    "SETTINGS_SECTION_METADATA_KEY",
    # API models for settings endpoints
    "SecretCreateRequest",
    "SecretItemResponse",
    "SecretsListResponse",
    "SettingProminence",
    "SettingsChoice",
    "SettingsFieldMetadata",
    "SettingsFieldSchema",
    "SettingsResponse",
    "SettingsSchema",
    "SettingsSectionMetadata",
    "SettingsSectionSchema",
    "SettingsUpdateRequest",
    "VerificationSettings",
    "apply_agent_settings_diff",
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
    if name in _MODEL_EXPORTS:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
