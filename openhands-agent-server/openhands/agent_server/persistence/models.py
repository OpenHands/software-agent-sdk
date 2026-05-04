"""Pydantic models for persisted settings and secrets.

These models mirror the structure used in OpenHands app-server for consistency,
allowing the agent-server to be used standalone or as a drop-in replacement
for the Cloud API's settings/secrets endpoints.
"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SerializationInfo,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from openhands.sdk.settings import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    AgentSettings,
    AgentSettingsConfig,
    ConversationSettings,
    default_agent_settings,
)
from openhands.sdk.settings.model import (
    _AGENT_SETTINGS_MIGRATIONS,
    _apply_persisted_migrations,
)
from openhands.sdk.utils.pydantic_secrets import serialize_secret, validate_secret


class SettingsUpdatePayload(TypedDict, total=False):
    """Typed payload for PersistedSettings.update() method."""

    agent_settings_diff: dict[str, Any]
    conversation_settings_diff: dict[str, Any]


class PersistedSettings(BaseModel):
    """Persisted settings for agent server.

    Agent settings (LLM config, MCP config, condenser) live in ``agent_settings``.
    Conversation settings (max_iterations, confirmation_mode) live in
    ``conversation_settings``.
    """

    agent_settings: AgentSettingsConfig = Field(default_factory=default_agent_settings)
    conversation_settings: ConversationSettings = Field(
        default_factory=ConversationSettings
    )

    model_config = ConfigDict(populate_by_name=True)

    @property
    def llm_api_key_is_set(self) -> bool:
        """Check if an LLM API key is configured."""
        raw = self.agent_settings.llm.api_key
        if raw is None:
            return False
        secret_value = (
            raw.get_secret_value() if isinstance(raw, SecretStr) else str(raw)
        )
        return bool(secret_value and secret_value.strip())

    def update(self, payload: SettingsUpdatePayload) -> None:
        """Apply a batch of changes from a nested dict.

        Accepts ``agent_settings_diff`` and ``conversation_settings_diff``
        for partial updates. Uses ``from_persisted()`` to apply any schema
        migrations if the incoming diff contains an older schema version.
        """
        from openhands.agent_server.persistence.utils import deep_merge

        agent_update = payload.get("agent_settings_diff")
        if isinstance(agent_update, dict):
            merged = deep_merge(
                self.agent_settings.model_dump(
                    mode="json", context={"expose_secrets": True}
                ),
                agent_update,
            )
            # Use from_persisted to handle potential schema migrations
            self.agent_settings = AgentSettings.from_persisted(merged)

        conv_update = payload.get("conversation_settings_diff")
        if isinstance(conv_update, dict):
            merged = deep_merge(
                self.conversation_settings.model_dump(mode="json"),
                conv_update,
            )
            # Use from_persisted to handle potential schema migrations
            self.conversation_settings = ConversationSettings.from_persisted(merged)

    @field_serializer("agent_settings")
    def agent_settings_serializer(
        self,
        agent_settings: AgentSettingsConfig,
        info: SerializationInfo,
    ) -> dict[str, Any]:
        # Pass through the full context (cipher, expose_secrets) to AgentSettings
        # This ensures secrets are properly encrypted/exposed based on context
        return agent_settings.model_dump(mode="json", context=info.context)

    @model_validator(mode="before")
    @classmethod
    def _normalize_inputs(cls, data: dict | object) -> dict | object:
        """Normalize inputs during deserialization.

        Applies schema migrations for both agent and conversation settings,
        ensuring forward compatibility when loading settings files saved with
        older schema versions.

        Note: We keep agent_settings as a dict here so that Pydantic's normal
        validation handles it with context. This allows cipher-based decryption
        to work properly through nested field validators (e.g., LLM._validate_secrets).
        """
        if not isinstance(data, dict):
            return data

        # Apply migrations for agent_settings but keep as dict
        # The dict will be validated by Pydantic with context for decryption
        agent_settings = data.get("agent_settings")
        if isinstance(agent_settings, dict):
            coerced = _coerce_dict_secrets(agent_settings)
            # Apply migrations only, return dict for Pydantic to validate with context
            migrated = _apply_persisted_migrations(
                coerced,
                current_version=AGENT_SETTINGS_SCHEMA_VERSION,
                migrations=_AGENT_SETTINGS_MIGRATIONS,
                payload_name="AgentSettings",
            )
            data["agent_settings"] = migrated

        # Apply migrations for conversation_settings
        conv_settings = data.get("conversation_settings")
        if isinstance(conv_settings, dict):
            data["conversation_settings"] = ConversationSettings.from_persisted(
                conv_settings
            )

        return data


class CustomSecret(BaseModel):
    """A custom secret with name, value, and optional description."""

    name: str
    secret: SecretStr
    description: str | None = None

    @field_validator("secret")
    @classmethod
    def _validate_secret(
        cls, v: str | SecretStr | None, info: ValidationInfo
    ) -> SecretStr | None:
        return validate_secret(v, info)

    @field_serializer("secret", when_used="always")
    def _serialize_secret(self, v: SecretStr | None, info: SerializationInfo):
        return serialize_secret(v, info)

    @classmethod
    def from_value(cls, value: dict[str, Any] | str) -> CustomSecret:
        """Create from dict or plain string value."""
        if isinstance(value, str):
            return cls(name="", secret=SecretStr(value))
        return cls(
            name=value.get("name", ""),
            secret=SecretStr(value.get("secret", "")),
            description=value.get("description"),
        )


class Secrets(BaseModel):
    """Model for storing custom secrets.

    Unlike OpenHands app-server which also stores provider tokens,
    the agent-server only stores custom secrets since it doesn't
    integrate with OAuth providers directly.
    """

    custom_secrets: dict[str, CustomSecret] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    def get_env_vars(self) -> dict[str, str]:
        """Get secrets as environment variables dict."""
        return {
            name: secret.secret.get_secret_value()
            for name, secret in self.custom_secrets.items()
        }

    def get_descriptions(self) -> dict[str, str | None]:
        """Get secret name to description mapping."""
        return {
            name: secret.description for name, secret in self.custom_secrets.items()
        }

    @field_serializer("custom_secrets")
    def custom_secrets_serializer(
        self, custom_secrets: dict[str, CustomSecret], info: SerializationInfo
    ) -> dict[str, dict[str, Any]]:
        # Delegate to CustomSecret.model_dump which uses serialize_secret
        # This ensures cipher context flows through for encryption
        result = {}
        for name, secret in custom_secrets.items():
            result[name] = secret.model_dump(mode="json", context=info.context)
        return result

    @model_validator(mode="before")
    @classmethod
    def _normalize_inputs(cls, data: dict | object) -> dict | object:
        """Normalize dict inputs to the expected structure.

        Note: We deliberately keep values as raw strings/dicts here so that
        Pydantic's field validators can handle cipher-based decryption via
        the validation context. Wrapping in SecretStr here would bypass the
        validate_secret() call that handles decryption.
        """
        if not isinstance(data, dict):
            return data

        custom_secrets = data.get("custom_secrets")
        if isinstance(custom_secrets, dict):
            converted = {}
            for name, value in custom_secrets.items():
                if isinstance(value, CustomSecret):
                    converted[name] = value
                elif isinstance(value, dict):
                    # Keep as dict - let Pydantic handle validation with context
                    converted[name] = {
                        "name": name,
                        "secret": value.get("secret", ""),
                        "description": value.get("description"),
                    }
                elif isinstance(value, str):
                    converted[name] = {
                        "name": name,
                        "secret": value,
                        "description": None,
                    }
            data["custom_secrets"] = converted

        return data


# ── Response Models for API ──────────────────────────────────────────────


class CustomSecretCreate(BaseModel):
    """Request model for creating a custom secret."""

    name: str
    value: SecretStr
    description: str | None = None


class CustomSecretResponse(BaseModel):
    """Response model for a custom secret (without value)."""

    name: str
    description: str | None = None


class SecretsResponse(BaseModel):
    """Response model listing available secrets."""

    secrets: list[CustomSecretResponse]


# ── Helper Functions ─────────────────────────────────────────────────────


def _coerce_dict_secrets(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively coerce SecretStr leaves to plain values."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = _coerce_dict_secrets(v)
        elif isinstance(v, SecretStr):
            out[k] = v.get_secret_value()
        else:
            out[k] = v
    return out
