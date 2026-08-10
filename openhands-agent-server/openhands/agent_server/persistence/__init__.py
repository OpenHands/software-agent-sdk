"""Persistence module for settings and secrets storage.

Note: API request/response models (SecretCreateRequest, SecretItemResponse,
SecretsListResponse, SettingsResponse, SettingsUpdateRequest) are defined
in the SDK to enable sharing between SDK clients and agent-server.
See: openhands.sdk.settings.api_models
"""

from openhands.agent_server.persistence.models import (
    CONNECTIONS_SCHEMA_VERSION,
    LLM_SECRET_REF_PREFIX,
    PERSISTED_SETTINGS_SCHEMA_VERSION,
    SECRET_NAME_PATTERN,
    WORKSPACES_SCHEMA_VERSION,
    CustomSecret,
    PersistedConnections,
    PersistedSettings,
    PersistedWorkspaces,
    ProviderConnection,
    Secrets,
    SettingsUpdatePayload,
    WorkspaceItem,
    WorkspaceParentItem,
    llm_secret_ref,
    parse_llm_secret_ref,
)
from openhands.agent_server.persistence.store import (
    ConnectionsStore,
    FileConnectionsStore,
    FileSecretsStore,
    FileSettingsStore,
    FileWorkspacesStore,
    SecretsStore,
    SettingsStore,
    WorkspacesStore,
    get_agent_profile_store,
    get_connections_store,
    get_llm_profile_store,
    get_secrets_store,
    get_settings_store,
    get_workspaces_store,
    reset_stores,
)


__all__ = [
    # Constants
    "CONNECTIONS_SCHEMA_VERSION",
    "LLM_SECRET_REF_PREFIX",
    "PERSISTED_SETTINGS_SCHEMA_VERSION",
    "SECRET_NAME_PATTERN",
    "WORKSPACES_SCHEMA_VERSION",
    # Models
    "CustomSecret",
    "PersistedConnections",
    "PersistedSettings",
    "PersistedWorkspaces",
    "ProviderConnection",
    "Secrets",
    "SettingsUpdatePayload",
    "WorkspaceItem",
    "WorkspaceParentItem",
    "llm_secret_ref",
    "parse_llm_secret_ref",
    # Stores
    "FileConnectionsStore",
    "FileSecretsStore",
    "FileSettingsStore",
    "FileWorkspacesStore",
    "ConnectionsStore",
    "SecretsStore",
    "SettingsStore",
    "WorkspacesStore",
    "get_agent_profile_store",
    "get_connections_store",
    "get_llm_profile_store",
    "get_secrets_store",
    "get_settings_store",
    "get_workspaces_store",
    "reset_stores",
]
