"""Pydantic models for persisted settings and secrets.

These models mirror the structure used in OpenHands app-server for consistency,
allowing the agent-server to be used standalone or as a drop-in replacement
for the Cloud API's settings/secrets endpoints.
"""

from __future__ import annotations

import re
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


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overlay dict into base dict.

    For nested dicts, merges recursively. For other types, overlay wins.
    """
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


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

        Thread Safety:
            This method is NOT thread-safe for concurrent in-memory updates.
            The assignments to ``agent_settings`` and ``conversation_settings``
            are not atomic. However, the router wraps calls via ``store.update()``
            which uses file locking to prevent concurrent updates at the I/O layer.
            Multiple ``PersistedSettings`` instances should NOT be shared across
            threads without external synchronization.

        Note:
            Secret values are temporarily exposed in memory during the merge
            operation. The merged dict is immediately consumed by model
            construction and not persisted or logged.

        Raises:
            ValueError: If validation fails (sanitized to avoid secret leakage).
        """
        agent_update = payload.get("agent_settings_diff")
        if isinstance(agent_update, dict):
            merged = _deep_merge(
                self.agent_settings.model_dump(
                    mode="json", context={"expose_secrets": "plaintext"}
                ),
                agent_update,
            )
            # Use from_persisted to handle potential schema migrations
            # Wrap in try-catch to prevent secrets from leaking in tracebacks
            try:
                self.agent_settings = AgentSettings.from_persisted(merged)
            except Exception as e:
                # Re-raise with sanitized message to avoid exposing secrets
                raise ValueError(
                    f"Failed to update agent settings: {type(e).__name__}"
                ) from None

        conv_update = payload.get("conversation_settings_diff")
        if isinstance(conv_update, dict):
            merged = _deep_merge(
                self.conversation_settings.model_dump(mode="json"),
                conv_update,
            )
            # Use from_persisted to handle potential schema migrations
            try:
                self.conversation_settings = ConversationSettings.from_persisted(merged)
            except Exception as e:
                raise ValueError(
                    f"Failed to update conversation settings: {type(e).__name__}"
                ) from None

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


_SECRET_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


class CustomSecret(BaseModel):
    """A custom secret with name, value, and optional description."""

    name: str
    secret: SecretStr | None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        """Validate secret name format for safety.

        Secret names are used as environment variable names and may be logged,
        so we enforce strict validation to prevent:
        - Path traversal (../, null bytes)
        - Log injection (control characters)
        - Shell injection (special characters)
        - Invalid env var names (starting with numbers, special chars)

        Note: The router also validates names, but this provides defense-in-depth
        for secrets created directly via the store (bypassing the HTTP layer).
        """
        if not _SECRET_NAME_PATTERN.match(v):
            raise ValueError(
                "Secret name must start with a letter, contain only "
                "letters/numbers/underscores, and be 1-64 characters"
            )
        return v

    @field_validator("secret")
    @classmethod
    def _validate_secret(
        cls, v: str | SecretStr | None, info: ValidationInfo
    ) -> SecretStr | None:
        return validate_secret(v, info)

    @field_serializer("secret", when_used="always")
    def _serialize_secret(self, v: SecretStr | None, info: SerializationInfo):
        return serialize_secret(v, info)


class Secrets(BaseModel):
    """Model for storing custom secrets.

    Unlike OpenHands app-server which also stores provider tokens,
    the agent-server only stores custom secrets since it doesn't
    integrate with OAuth providers directly.
    """

    custom_secrets: dict[str, CustomSecret] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    def get_env_vars(self) -> dict[str, str]:
        """Get secrets as environment variables dict.

        Safely extracts secret values, logging warnings for malformed secrets.
        """
        result: dict[str, str] = {}
        for name, secret in self.custom_secrets.items():
            if secret.secret is None:
                continue
            try:
                result[name] = secret.secret.get_secret_value()
            except Exception:
                # Log without exposing secret contents
                from openhands.sdk.logger import get_logger

                get_logger(__name__).warning(
                    f"Failed to extract secret '{name}' - skipping"
                )
        return result

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
                    # Note: Use None instead of "" for missing secret to preserve
                    # distinction between "empty secret" and "missing secret"
                    converted[name] = {
                        "name": name,
                        "secret": value.get("secret"),  # None if missing
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
    """Recursively coerce SecretStr leaves to plain values.

    Note: SecretStr extraction is wrapped in error handling to prevent secret
    values from leaking in exception tracebacks.
    """
    from openhands.sdk.logger import get_logger

    _logger = get_logger(__name__)
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = _coerce_dict_secrets(v)
        elif isinstance(v, SecretStr):
            try:
                out[k] = v.get_secret_value()
            except Exception:
                _logger.warning(
                    f"Failed to extract secret value for key '{k}' - skipping"
                )
                out[k] = None
        else:
            out[k] = v
    return out
