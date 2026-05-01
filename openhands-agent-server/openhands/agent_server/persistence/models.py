"""Pydantic models for persisted settings and secrets.

These models mirror the structure used in OpenHands app-server for consistency,
allowing the agent-server to be used standalone or as a drop-in replacement
for the Cloud API's settings/secrets endpoints.
"""

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SerializationInfo,
    field_serializer,
    model_validator,
)

from openhands.sdk.settings import (
    AgentSettingsConfig,
    ConversationSettings,
    default_agent_settings,
    validate_agent_settings,
)


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

    def update(self, payload: dict[str, Any]) -> None:
        """Apply a batch of changes from a nested dict.

        Accepts ``agent_settings_diff`` and ``conversation_settings_diff``
        for partial updates.
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
            self.agent_settings = validate_agent_settings(merged)

        conv_update = payload.get("conversation_settings_diff")
        if isinstance(conv_update, dict):
            merged = deep_merge(
                self.conversation_settings.model_dump(mode="json"),
                conv_update,
            )
            self.conversation_settings = ConversationSettings.model_validate(merged)

    @field_serializer("agent_settings")
    def agent_settings_serializer(
        self,
        agent_settings: AgentSettingsConfig,
        info: SerializationInfo,
    ) -> dict[str, Any]:
        context = info.context or {}
        if context.get("expose_secrets", False):
            return agent_settings.model_dump(
                mode="json", context={"expose_secrets": True}
            )
        return agent_settings.model_dump(mode="json")

    @model_validator(mode="before")
    @classmethod
    def _normalize_inputs(cls, data: dict | object) -> dict | object:
        """Normalize inputs during deserialization."""
        if not isinstance(data, dict):
            return data

        # Coerce SecretStr leaves to plain strings for agent_settings
        agent_settings = data.get("agent_settings")
        if isinstance(agent_settings, dict):
            data["agent_settings"] = _coerce_dict_secrets(agent_settings)

        return data


class CustomSecret(BaseModel):
    """A custom secret with name, value, and optional description."""

    name: str
    secret: SecretStr
    description: str | None = None

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
        expose = info.context and info.context.get("expose_secrets", False)
        result = {}
        for name, secret in custom_secrets.items():
            result[name] = {
                "secret": secret.secret.get_secret_value() if expose else "**********",
                "description": secret.description,
            }
        return result

    @model_validator(mode="before")
    @classmethod
    def _normalize_inputs(cls, data: dict | object) -> dict | object:
        """Convert dict inputs to CustomSecret instances."""
        if not isinstance(data, dict):
            return data

        custom_secrets = data.get("custom_secrets")
        if isinstance(custom_secrets, dict):
            converted = {}
            for name, value in custom_secrets.items():
                if isinstance(value, CustomSecret):
                    converted[name] = value
                elif isinstance(value, dict):
                    converted[name] = CustomSecret(
                        name=name,
                        secret=SecretStr(value.get("secret", "")),
                        description=value.get("description"),
                    )
                elif isinstance(value, str):
                    converted[name] = CustomSecret(
                        name=name, secret=SecretStr(value), description=None
                    )
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
