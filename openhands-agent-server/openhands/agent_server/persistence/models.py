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
    AgentSettingsConfig,
    ConversationSettings,
    default_agent_settings,
    validate_agent_settings,
)
from openhands.sdk.utils.pydantic_secrets import serialize_secret, validate_secret


class SettingsUpdatePayload(TypedDict, total=False):
    """Typed payload for PersistedSettings.update() method.

    The ``*_diff`` dicts are deep-merged via :func:`_deep_merge`: nested
    objects merge recursively, and a ``None`` value *inside a nested map*
    deletes that entry (the "unset" primitive) — e.g. send
    ``{"acp_env": {"NAME": None}}`` to drop one env-var without re-sending the
    whole map. A ``None`` on a top-level *field* is not treated as delete; it
    flows to validation as before.
    """

    agent_settings_diff: dict[str, Any]
    conversation_settings_diff: dict[str, Any]
    active_profile: str | None


def _deep_merge(
    base: dict[str, Any],
    overlay: dict[str, Any],
    *,
    unset_nulls: bool = False,
) -> dict[str, Any]:
    """Recursively merge ``overlay`` into ``base``.

    - Nested dicts are merged recursively.
    - **Inside a nested map** a ``None`` value **removes** that key — the
      "unset" primitive a plain deep-merge lacks. It lets a
      ``PATCH /api/settings`` diff delete a single map entry (one
      ``acp_env`` / MCP ``env`` key) without round-tripping the whole map::

          {"agent_settings_diff": {"acp_env": {"STALE_KEY": null}}}

    - **At the top level** (a settings *field* like ``confirmation_mode`` or
      ``acp_env`` itself) a ``None`` is left as-is and flows to model
      validation — exactly as before this primitive existed. So a stray
      ``{"confirmation_mode": null}`` still fails loudly (422) instead of
      silently resetting a field to its default. This scoping is deliberate:
      ``unset`` is for *entries within* a map, not for nulling whole fields.
    - For any other scalar/list value, the overlay wins.

    ``unset_nulls`` is ``False`` for the top-level call and ``True`` for every
    recursive (nested) call — that's what draws the field-vs-entry line above.

    Corner case: a key **absent from** ``base`` whose overlay value is a dict
    is assigned wholesale (no recursion), so any ``null`` entries inside that
    dict are stored as-is rather than treated as deletes. This is intentional
    — you can't delete an entry from a map that doesn't exist yet — but it
    means "initialize a new map and unset a key within it" in one diff won't
    strip the null; downstream validation handles the resulting value.
    """
    result = dict(base)
    for key, value in overlay.items():
        if value is None and unset_nulls:
            # Nested map entry: a null member removes the key (no-op if absent).
            result.pop(key, None)
        elif (
            key in result and isinstance(result[key], dict) and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value, unset_nulls=True)
        else:
            # Top-level null (unset_nulls=False) falls here: set as-is and let
            # model validation decide (preserves pre-existing behavior).
            result[key] = value
    return result


PERSISTED_SETTINGS_SCHEMA_VERSION = 1


class PersistedSettings(BaseModel):
    """Persisted settings for agent server.

    Agent settings (LLM config, MCP config, condenser) live in ``agent_settings``.
    Conversation settings (max_iterations, confirmation_mode) live in
    ``conversation_settings``.

    The ``active_profile`` field tracks which LLM profile was last activated,
    allowing frontends to display which profile is currently in use.
    """

    schema_version: int = Field(
        default=PERSISTED_SETTINGS_SCHEMA_VERSION,
        description="Persisted settings file schema version.",
    )

    agent_settings: AgentSettingsConfig = Field(default_factory=default_agent_settings)
    conversation_settings: ConversationSettings = Field(
        default_factory=ConversationSettings
    )
    active_profile: str | None = Field(
        default=None,
        description="Name of the currently active LLM profile.",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        revalidate_instances="always",
    )

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

        Accepts ``agent_settings_diff``, ``conversation_settings_diff``, and
        ``active_profile`` for partial updates. Uses ``from_persisted()`` to
        apply any schema migrations if the incoming diff contains an older
        schema version.

        When ``agent_kind`` changes in the diff, the update is treated as a
        variant replacement: the incoming diff is validated as-is rather than
        merged with the old variant's fields. Same-kind updates retain deep-merge
        behavior for incremental field edits.

        Thread Safety:
            This method is NOT thread-safe for concurrent in-memory updates.
            The assignments to ``agent_settings`` and ``conversation_settings``
            are not atomic. However, the router wraps calls via ``store.update()``
            which uses file locking to prevent concurrent updates at the I/O layer.
            Multiple ``PersistedSettings`` instances should NOT be shared across
            threads without external synchronization.

        Atomicity:
            Both updates are validated before any mutations occur. If either
            validation fails, the object remains unchanged.

        Note:
            Secret values are temporarily exposed in memory during the merge
            operation. Merged dicts are cleared after use to minimize exposure.

        Raises:
            ValueError: If validation fails (sanitized to avoid secret leakage).
        """
        agent_update = payload.get("agent_settings_diff")
        conv_update = payload.get("conversation_settings_diff")

        # Phase 1: Validate both updates before any mutations
        new_agent: AgentSettingsConfig | None = None
        new_conv: ConversationSettings | None = None
        agent_merged: dict | None = None
        conv_merged: dict | None = None

        try:
            if isinstance(agent_update, dict):
                # Check if this is a variant (agent_kind) switch
                old_kind = self.agent_settings.agent_kind
                new_kind = agent_update.get("agent_kind")
                is_kind_switch = new_kind is not None and new_kind != old_kind

                if is_kind_switch:
                    # Variant replacement: validate the diff as-is rather than
                    # deep-merging it onto the old variant. A kind switch picks a
                    # different member of the AgentSettingsConfig union, and the
                    # old variant's serialized fields are not a valid base for the
                    # new one (e.g. ACP's acp_command has no place in
                    # OpenHandsAgentSettings and would fail validation).
                    #
                    # Consequence (intentional): fields the two variants happen to
                    # share (e.g. ``llm``) are NOT carried over — they fall back to
                    # the new variant's defaults unless the caller restates them in
                    # this same diff. Switching kinds is a fresh start on the new
                    # variant, mirroring the frontend's "fresh base on kind switch"
                    # behaviour. Callers that want to preserve a shared field must
                    # include it in the switch payload.
                    agent_merged = agent_update
                else:
                    # Same-kind update: deep-merge for incremental field edits
                    agent_merged = _deep_merge(
                        self.agent_settings.model_dump(
                            mode="json", context={"expose_secrets": "plaintext"}
                        ),
                        agent_update,
                    )
                try:
                    new_agent = validate_agent_settings(agent_merged)
                except Exception as e:
                    # Use 'from None' to break exception chain - the original
                    # exception may contain secret values in Pydantic errors
                    raise ValueError(
                        f"Failed to update agent settings: {type(e).__name__}"
                    ) from None

            if isinstance(conv_update, dict):
                conv_merged = _deep_merge(
                    self.conversation_settings.model_dump(mode="json"),
                    conv_update,
                )
                try:
                    new_conv = ConversationSettings.from_persisted(conv_merged)
                except Exception as e:
                    # Use 'from None' to break exception chain - see above
                    raise ValueError(
                        f"Failed to update conversation settings: {type(e).__name__}"
                    ) from None

            # Phase 2: Apply validated changes atomically
            if new_agent is not None:
                self.agent_settings = new_agent
            if new_conv is not None:
                self.conversation_settings = new_conv

            # Update active_profile if explicitly provided (including None to clear)
            if "active_profile" in payload:
                self.active_profile = payload["active_profile"]
        finally:
            # Clear merged dicts to minimize plaintext exposure window
            if agent_merged is not None:
                agent_merged.clear()
            if conv_merged is not None:
                conv_merged.clear()

    @classmethod
    def from_persisted(
        cls, data: Any, *, context: dict[str, Any] | None = None
    ) -> PersistedSettings:
        """Load persisted settings, applying top-level and nested migrations."""
        if not isinstance(data, dict):
            return cls.model_validate(data, context=context)

        payload = dict(data)
        version = payload.get("schema_version", 0) or 0
        if type(version) is not int:
            raise ValueError("PersistedSettings schema_version must be an integer")
        if version > PERSISTED_SETTINGS_SCHEMA_VERSION:
            raise ValueError(
                "PersistedSettings schema_version "
                f"{version} is newer than supported version "
                f"{PERSISTED_SETTINGS_SCHEMA_VERSION}"
            )
        payload["schema_version"] = PERSISTED_SETTINGS_SCHEMA_VERSION
        return cls.model_validate(payload, context=context)

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
    def _normalize_inputs(
        cls, data: dict | object, info: ValidationInfo
    ) -> dict | object:
        """Normalize inputs during deserialization.

        Applies schema migrations for both agent and conversation settings,
        ensuring forward compatibility when loading settings files saved with
        older schema versions.

        Agent settings are normalized through ``validate_agent_settings``
        so the same migration entry point is used for settings files and direct
        SDK callers. The validation context is forwarded so cipher-based secret
        decryption still works during the nested settings validation.
        """
        if not isinstance(data, dict):
            return data

        agent_settings = data.get("agent_settings")
        if isinstance(agent_settings, dict):
            coerced = _coerce_dict_secrets(agent_settings)
            data["agent_settings"] = validate_agent_settings(
                coerced,
                context=info.context,
            )

        # Apply migrations for conversation_settings
        conv_settings = data.get("conversation_settings")
        if isinstance(conv_settings, dict):
            data["conversation_settings"] = ConversationSettings.from_persisted(
                conv_settings
            )

        return data


# Validation pattern for secret names - exported for use by settings_router
# Names: start with letter, alphanumeric + underscores, 1-64 chars
SECRET_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


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
        if not SECRET_NAME_PATTERN.match(v):
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


# ── Workspaces ───────────────────────────────────────────────────────────

WORKSPACES_SCHEMA_VERSION = 1


class WorkspaceItem(BaseModel):
    # ``id`` is opaque server-side (dedupe is by ``path``), but the GUI sets
    # ``id == path`` for both workspaces and parents. Capping ``id`` below
    # ``path`` would 422 long but otherwise-valid filesystem paths, so the
    # two caps must stay aligned.
    id: str = Field(..., min_length=1, max_length=4096)
    name: str = Field(..., min_length=1, max_length=256)
    path: str = Field(..., min_length=1, max_length=4096)
    parent_path: str | None = Field(default=None, alias="parentPath", max_length=4096)
    model_config = ConfigDict(populate_by_name=True)


class WorkspaceParentItem(BaseModel):
    # See ``WorkspaceItem.id`` — keep ``id`` and ``path`` caps aligned.
    id: str = Field(..., min_length=1, max_length=4096)
    name: str = Field(..., min_length=1, max_length=256)
    path: str = Field(..., min_length=1, max_length=4096)


class PersistedWorkspaces(BaseModel):
    schema_version: int = Field(default=WORKSPACES_SCHEMA_VERSION)
    workspaces: list[WorkspaceItem] = Field(default_factory=list)
    workspace_parents: list[WorkspaceParentItem] = Field(
        default_factory=list, alias="workspaceParents"
    )
    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_persisted(cls, data: Any) -> PersistedWorkspaces:
        if not isinstance(data, dict):
            return cls.model_validate(data)
        payload = dict(data)
        version = payload.get("schema_version", WORKSPACES_SCHEMA_VERSION)
        if not isinstance(version, int):
            raise ValueError("PersistedWorkspaces schema_version must be an integer")
        if version > WORKSPACES_SCHEMA_VERSION:
            raise ValueError(
                f"PersistedWorkspaces schema_version {version} is newer than "
                f"supported {WORKSPACES_SCHEMA_VERSION}"
            )
        payload["schema_version"] = WORKSPACES_SCHEMA_VERSION
        return cls.model_validate(payload)


# ── Helper Functions ─────────────────────────────────────────────────────
#
# Note: API request/response models have been moved to the SDK to enable
# sharing between SDK clients and the agent-server. See:
#   openhands.sdk.settings.api_models (SecretCreateRequest, SecretItemResponse, etc.)


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
