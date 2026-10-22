from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .acp_providers import (
    ACP_PROVIDERS,
    ACPFileSecretSpec,
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
    from .acp_providers import ACPModelOption
    from .document import (
        SETTINGS_DOCUMENT_SCHEMA_VERSION,
        LLMProfileDocument,
        ProductSettings,
        ProductSettingsPatch,
        ProviderConnectionDocument,
        SettingsDocument,
        SettingsDocumentPatch,
        SettingsProfilePointers,
        SettingsProfilePointersPatch,
        SettingsResources,
        SettingsResourcesPatch,
        SettingsSecretReference,
        export_settings_document_schema,
        register_settings_document_field,
    )
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

_DOCUMENT_EXPORTS = {
    "SETTINGS_DOCUMENT_SCHEMA_VERSION",
    "LLMProfileDocument",
    "ProductSettings",
    "ProductSettingsPatch",
    "ProviderConnectionDocument",
    "SettingsDocument",
    "SettingsDocumentPatch",
    "SettingsProfilePointers",
    "SettingsProfilePointersPatch",
    "SettingsResources",
    "SettingsResourcesPatch",
    "SettingsSecretReference",
    "export_settings_document_schema",
    "register_settings_document_field",
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
    "SETTINGS_DOCUMENT_SCHEMA_VERSION",
    "SETTINGS_METADATA_KEY",
    "SETTINGS_SECTION_METADATA_KEY",
    # API models for settings endpoints
    "LLMProfileDocument",
    "ProductSettings",
    "ProductSettingsPatch",
    "ProviderConnectionDocument",
    "SecretCreateRequest",
    "SecretItemResponse",
    "SecretsListResponse",
    "SettingProminence",
    "SettingsDocument",
    "SettingsDocumentPatch",
    "SettingsProfilePointers",
    "SettingsProfilePointersPatch",
    "SettingsResources",
    "SettingsResourcesPatch",
    "SettingsSecretReference",
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
    "export_settings_document_schema",
    "export_settings_schema",
    "register_settings_document_field",
    "field_meta",
    "get_acp_provider",
    "validate_agent_settings",
]


def __getattr__(name: str) -> Any:
    if name == "ACPModelOption":
        from .acp_providers import _deprecated_model_option

        return _deprecated_model_option()
    if name in _MODEL_EXPORTS:
        from . import model

        return getattr(model, name)
    if name in _DOCUMENT_EXPORTS:
        from .document import (
            SETTINGS_DOCUMENT_SCHEMA_VERSION,
            LLMProfileDocument,
            ProductSettings,
            ProductSettingsPatch,
            ProviderConnectionDocument,
            SettingsDocument,
            SettingsDocumentPatch,
            SettingsProfilePointers,
            SettingsProfilePointersPatch,
            SettingsResources,
            SettingsResourcesPatch,
            SettingsSecretReference,
            export_settings_document_schema,
            register_settings_document_field,
        )

        return {
            "SETTINGS_DOCUMENT_SCHEMA_VERSION": SETTINGS_DOCUMENT_SCHEMA_VERSION,
            "LLMProfileDocument": LLMProfileDocument,
            "ProductSettings": ProductSettings,
            "ProductSettingsPatch": ProductSettingsPatch,
            "ProviderConnectionDocument": ProviderConnectionDocument,
            "SettingsDocument": SettingsDocument,
            "SettingsDocumentPatch": SettingsDocumentPatch,
            "SettingsProfilePointers": SettingsProfilePointers,
            "SettingsProfilePointersPatch": SettingsProfilePointersPatch,
            "SettingsResources": SettingsResources,
            "SettingsResourcesPatch": SettingsResourcesPatch,
            "SettingsSecretReference": SettingsSecretReference,
            "export_settings_document_schema": export_settings_document_schema,
            "register_settings_document_field": register_settings_document_field,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
