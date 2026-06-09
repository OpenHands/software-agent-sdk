"""API request and response models for settings endpoints.

These models define the contract between SDK clients and agent-server settings
endpoints. They are defined in the SDK so both packages can share them without
circular dependencies (SDK cannot import from agent-server, but agent-server
can import from SDK).

Server-side usage:
    The agent-server imports these models and uses them as FastAPI response_model.

Client-side usage:
    RemoteWorkspace uses these models to validate responses from settings APIs.
    Use the typed accessor methods (``get_agent_settings()``,
    ``get_conversation_settings()``) to parse the raw dicts into typed models.

Note on dict fields:
    ``SettingsResponse`` uses ``dict[str, Any]`` for ``agent_settings`` and
    ``conversation_settings`` rather than typed models because the server needs
    to control how secrets are serialized (plaintext/encrypted/redacted) via
    serialization context. Typed Pydantic fields would lose this context during
    FastAPI's automatic JSON serialization.

    Clients that need type safety should use the accessor methods which validate
    the dicts into ``AgentSettingsConfig`` and ``ConversationSettings``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, SecretStr


if TYPE_CHECKING:
    from .model import AgentSettingsConfig, ConversationSettings


# ── Settings API Models ───────────────────────────────────────────────────


class SettingsResponse(BaseModel):
    """Response model for GET /api/settings.

    Contains the full settings payload including agent configuration,
    conversation settings, miscellaneous frontend-owned settings, and a flag
    indicating whether an LLM API key is set.

    The ``agent_settings`` and ``conversation_settings`` fields are raw dicts
    because the server controls secret serialization via context. Use the
    typed accessor methods for validation:

    Example::

        response = SettingsResponse.model_validate(api_response.json())
        agent = response.get_agent_settings()  # Returns AgentSettingsConfig
        conv = response.get_conversation_settings()  # Returns ConversationSettings

    ``misc_settings`` is an opaque container for frontend-owned data that the
    agent-server persists but does not interpret — see the docstring of
    :class:`PersistedSettings.misc_settings`.
    """

    agent_settings: dict[str, Any]
    conversation_settings: dict[str, Any]
    llm_api_key_is_set: bool
    misc_settings: dict[str, Any] = Field(default_factory=dict)

    def get_agent_settings(self) -> AgentSettingsConfig:
        """Parse and validate ``agent_settings`` into a typed model.

        Returns:
            The validated agent settings as either ``OpenHandsAgentSettings``
            or ``ACPAgentSettings`` depending on the ``agent_kind`` discriminator.
        """
        from .model import validate_agent_settings

        return validate_agent_settings(self.agent_settings)

    def get_conversation_settings(self) -> ConversationSettings:
        """Parse and validate ``conversation_settings`` into a typed model.

        Returns:
            The validated conversation settings.
        """
        from .model import ConversationSettings

        return ConversationSettings.from_persisted(self.conversation_settings)


class SettingsUpdateRequest(BaseModel):
    """Request model for PATCH /api/settings.

    Supports partial updates via diff objects that are deep-merged with
    existing settings. ``misc_settings_diff`` is deep-merged into the
    persisted ``misc_settings`` block with the same semantics as
    ``agent_settings_diff`` and ``conversation_settings_diff``: nested dicts
    merge recursively, and lists are replaced wholesale rather than merged.
    Because ``misc_settings`` is opaque to the agent-server, callers are
    responsible for the shape of what they store there.
    """

    agent_settings_diff: dict[str, Any] | None = None
    conversation_settings_diff: dict[str, Any] | None = None
    misc_settings_diff: dict[str, Any] | None = None


# ── Secrets API Models ────────────────────────────────────────────────────


class SecretItemResponse(BaseModel):
    """Response model for a secret item (without value).

    Used in list responses and as the response for create/update operations.
    """

    name: str
    description: str | None = None


class SecretsListResponse(BaseModel):
    """Response model for GET /api/settings/secrets.

    Lists all available secrets with their names and descriptions.
    Values are never included in list responses.
    """

    secrets: list[SecretItemResponse]


class SecretCreateRequest(BaseModel):
    """Request model for PUT /api/settings/secrets.

    Creates or updates a secret with the given name and value.
    """

    name: str
    value: SecretStr
    description: str | None = None
