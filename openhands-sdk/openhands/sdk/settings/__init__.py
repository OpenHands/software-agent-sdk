from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .acp_providers import (
    ACP_PROVIDERS,
    ACPProviderInfo,
    build_session_model_meta,
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
    from .update_models import (
        ACPAgentSettingsUpdate,
        AgentSettingsUpdate,
        CondenserSettingsUpdate,
        ConversationSettingsUpdate,
        LLMUpdate,
        OpenHandsAgentSettingsUpdate,
        VerificationSettingsUpdate,
        apply_agent_update,
        apply_conversation_update,
        validate_agent_settings_update,
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

# Lazy-loaded to avoid a circular import: ``update_models`` imports ``LLM`` and
# ``AgentContext`` which themselves import ``settings.metadata`` during module
# initialisation. Loading these eagerly here would re-enter
# ``settings/__init__.py`` while it's still being constructed.
_UPDATE_MODEL_EXPORTS = {
    "ACPAgentSettingsUpdate",
    "AgentSettingsUpdate",
    "CondenserSettingsUpdate",
    "ConversationSettingsUpdate",
    "LLMUpdate",
    "OpenHandsAgentSettingsUpdate",
    "VerificationSettingsUpdate",
    "apply_agent_update",
    "apply_conversation_update",
    "validate_agent_settings_update",
}

__all__ = [
    "ACP_PROVIDERS",
    "ACPProviderInfo",
    "build_session_model_meta",
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
    # Typed partial-update models (new endpoints)
    "ACPAgentSettingsUpdate",
    "AgentSettingsUpdate",
    "CondenserSettingsUpdate",
    "ConversationSettingsUpdate",
    "LLMUpdate",
    "OpenHandsAgentSettingsUpdate",
    "VerificationSettingsUpdate",
    "apply_agent_update",
    "apply_conversation_update",
    "validate_agent_settings_update",
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
            removed_in="1.24.0",
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
    if name in _UPDATE_MODEL_EXPORTS:
        from . import update_models

        return getattr(update_models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
