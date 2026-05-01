"""Persistence module for storing settings and secrets.

This module provides file-based storage for:
- Settings (agent settings, LLM config, MCP config, conversation settings)
- Secrets (custom secrets with name/value/description)

Following the same pattern as OpenHands app-server for consistency.
"""

from openhands.agent_server.persistence.models import (
    CustomSecret,
    CustomSecretCreate,
    CustomSecretResponse,
    PersistedSettings,
    Secrets,
    SecretsResponse,
)
from openhands.agent_server.persistence.store import (
    FileSecretsStore,
    FileSettingsStore,
    SecretsStore,
    SettingsStore,
    get_secrets_store,
    get_settings_store,
    reset_stores,
)


__all__ = [
    # Models
    "CustomSecret",
    "CustomSecretCreate",
    "CustomSecretResponse",
    "PersistedSettings",
    "Secrets",
    "SecretsResponse",
    # Stores
    "FileSecretsStore",
    "FileSettingsStore",
    "SecretsStore",
    "SettingsStore",
    "get_secrets_store",
    "get_settings_store",
    "reset_stores",
]
