from __future__ import annotations

import copy
import itertools
import shutil
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Literal,
    TypeVar,
    cast,
    get_args,
    get_origin,
)
from uuid import UUID

from fastmcp.mcp_config import MCPConfig
from pydantic import (
    BaseModel,
    Discriminator,
    Field,
    SecretStr,
    SerializationInfo,
    Tag,
    TypeAdapter,
    ValidationInfo,
    field_serializer,
    field_validator,
)
from pydantic.fields import FieldInfo

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.request import SendMessageRequest
from openhands.sdk.conversation.types import (
    ConversationObservabilityMetadata,
    ConversationObservabilityTags,
)
from openhands.sdk.hooks import HookConfig
from openhands.sdk.llm import LLM
from openhands.sdk.llm.utils.openhands_provider import (
    canonicalize_openhands_llm_payload,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.plugin import PluginSource
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.tool import Tool
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.deprecation import warn_deprecated
from openhands.sdk.utils.pydantic_secrets import (
    MissingCipherError,
    decrypt_str_with_cipher_or_keep,
    resolve_expose_mode,
    serialize_secret,
    validate_secret,
)
from openhands.sdk.utils.redact import sanitize_dict
from openhands.sdk.workspace import LocalWorkspace

from .acp_providers import (
    ACPFileSecretSpec,
    ACPProviderInfo,
    default_acp_file_secrets,
    get_acp_provider,
)
from .metadata import (
    SETTINGS_METADATA_KEY,
    SETTINGS_SECTION_METADATA_KEY,
    SettingProminence,
    SettingsFieldMetadata,
    SettingsSectionMetadata,
)


if TYPE_CHECKING:
    from openhands.sdk.agent import ACPAgent, Agent
    from openhands.sdk.agent.base import AgentBase
    from openhands.sdk.context.condenser import CondenserBase, LLMSummarizingCondenser
    from openhands.sdk.critic.base import CriticBase


logger = get_logger(__name__)


def _walk_mcp_secret_values(
    config: dict[str, Any],
    transform: Callable[[str], str],
) -> dict[str, Any]:
    """Return a copy of ``config`` with ``transform`` applied to every string
    value inside each MCP server's ``env`` / ``headers``. Does not mutate input."""
    config = copy.deepcopy(config)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return config
    for server in servers.values():
        if not isinstance(server, dict):
            continue
        for key in ("env", "headers"):
            mapping = server.get(key)
            if not isinstance(mapping, dict):
                continue
            server[key] = {
                k: (transform(v) if isinstance(v, str) else v)
                for k, v in mapping.items()
            }
    return config


def _decrypt_mcp_value_or_keep(cipher: Cipher, value: str) -> str:
    """Decrypt a single MCP ``env`` / ``headers`` value when it is a
    Fernet token. Thin local wrapper that binds the user-facing
    log description; the leaf decryption lives in
    :func:`decrypt_str_with_cipher_or_keep` and is shared with every
    other dict-of-string secret-bearing field."""
    return decrypt_str_with_cipher_or_keep(cipher, value, description="MCP env/headers")


# ---------------------------------------------------------------------------
# Shared ``mcp_config`` field (de)serialization, used verbatim by every
# settings variant that exposes an ``mcp_config: MCPConfig | None`` field
# (``OpenHandsAgentSettings`` and ``ACPAgentSettings``). Kept here as plain
# functions so the per-class ``@field_validator`` / ``@field_serializer``
# stubs — which pydantic requires to live on each model — stay one-liners and
# the encrypt/decrypt logic has a single source of truth.
# ---------------------------------------------------------------------------


def normalize_empty_mcp_config(value: Any) -> Any:
    """Coerce an empty/absent ``mcp_config`` to ``None`` (else pass through)."""
    return None if value in (None, {}) else value


def decrypt_mcp_config_secrets(value: Any, info: ValidationInfo) -> Any:
    """Decrypt MCP ``env`` / ``headers`` values when a cipher is in context.

    The on-disk load path. Values that aren't valid Fernet tokens pass through
    as plaintext (e.g. migrating from a build that wrote them unencrypted).
    Mirrors :func:`serialize_mcp_config`'s per-value encryption.
    """
    if not isinstance(value, dict):
        return value
    cipher: Cipher | None = info.context.get("cipher") if info.context else None
    if cipher is None:
        return value
    return _walk_mcp_secret_values(
        value, lambda v: _decrypt_mcp_value_or_keep(cipher, v)
    )


def serialize_mcp_config(
    value: MCPConfig | None, info: SerializationInfo
) -> dict[str, Any]:
    """Serialize an ``mcp_config`` field, masking/encrypting env+headers per
    the active expose mode (``plaintext`` / ``encrypted`` / redacted)."""
    if value is None:
        return {}
    dumped = value.model_dump(exclude_none=True, exclude_defaults=True)
    ctx = info.context or {}
    mode = resolve_expose_mode(ctx)

    if mode == "plaintext":
        return dumped

    if mode == "encrypted":
        cipher: Cipher | None = ctx.get("cipher")
        if cipher is None:
            raise MissingCipherError(
                "Cannot encrypt MCP env/headers: no cipher configured. "
                "Set OH_SECRET_KEY environment variable."
            )
        # cipher.encrypt returns None only for None input; SecretStr(v) never is.
        return _walk_mcp_secret_values(
            dumped, lambda v: cast(str, cipher.encrypt(SecretStr(v)))
        )

    return sanitize_dict(dumped)


SettingsValueType = Literal[
    "string",
    "integer",
    "number",
    "boolean",
    "array",
    "object",
]
SettingsChoiceValue = bool | int | float | str


class SettingsChoice(BaseModel):
    value: SettingsChoiceValue
    label: str


class SettingsFieldSchema(BaseModel):
    key: str
    label: str
    description: str | None = None
    section: str
    section_label: str
    value_type: SettingsValueType
    default: Any = None
    prominence: SettingProminence = SettingProminence.MINOR
    depends_on: list[str] = Field(default_factory=list)
    secret: bool = False
    choices: list[SettingsChoice] = Field(default_factory=list)
    variant: str | None = Field(
        default=None,
        description=(
            "When set, the field only applies to the named ``AgentSettings`` "
            "variant (``'openhands'`` or ``'acp'``). The GUI filters fields by the "
            "user's current variant; fields with ``variant=None`` are shown "
            "regardless."
        ),
    )


class SettingsSectionSchema(BaseModel):
    key: str
    label: str
    fields: list[SettingsFieldSchema]
    variant: str | None = Field(
        default=None,
        description=(
            "When set, this section only applies to the named ``AgentSettings`` "
            "variant (e.g. ``'openhands'`` or ``'acp'``). The GUI filters sections by "
            "the current ``agent_kind`` value; sections with ``variant=None`` "
            "are always shown."
        ),
    )


class SettingsSchema(BaseModel):
    model_name: str
    sections: list[SettingsSectionSchema]


CriticMode = Literal["finish_and_message", "all_actions"]
SecurityAnalyzerType = Literal["llm", "none"]


class CondenserSettings(BaseModel):
    """Shared base for condenser-settings variants.

    Use :data:`CondenserSettingsConfig` for fields that may hold any supported
    condenser-settings variant.
    """

    enabled: bool = Field(
        default=True,
        description="Enable conversation memory condensation.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Enable memory condensation",
                prominence=SettingProminence.CRITICAL,
            ).model_dump()
        },
    )
    max_size: int = Field(
        default=240,
        ge=20,
        description=(
            "Maximum number of events kept before the condenser runs. "
            "Kept on the base settings class for compatibility; concrete "
            "condenser-settings variants may opt out when this does not apply."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Max size",
                prominence=SettingProminence.MINOR,
                depends_on=("enabled",),
            ).model_dump()
        },
    )

    def build_condenser(self, llm: LLM) -> CondenserBase | None:
        """Create a condenser from these settings, or ``None`` if disabled."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement build_condenser()"
        )


class LLMSummarizingCondenserSettings(CondenserSettings):
    """Settings for the default LLM summarizing condenser."""

    condenser_kind: Literal["llm_summarizing"] = Field(
        default="llm_summarizing",
        description=(
            "Discriminator for the condenser settings union. ``'llm_summarizing'`` "
            "selects the default LLM summarizing condenser."
        ),
    )
    max_tokens: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Maximum number of tokens allowed before the condenser runs. "
            "When unset, condensation is only based on event count."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Max tokens",
                prominence=SettingProminence.MINOR,
                depends_on=("enabled",),
            ).model_dump()
        },
    )
    keep_first: int = Field(
        default=2,
        ge=0,
        description="Minimum number of initial events to preserve before condensation.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Keep first",
                prominence=SettingProminence.MINOR,
                depends_on=("enabled",),
            ).model_dump()
        },
    )
    minimum_progress: float = Field(
        default=0.1,
        gt=0.0,
        lt=1.0,
        description=(
            "Minimum fraction of events that must be condensed for condensation "
            "to be considered successful."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Minimum progress",
                prominence=SettingProminence.MINOR,
                depends_on=("enabled",),
            ).model_dump()
        },
    )
    hard_context_reset_max_retries: int = Field(
        default=5,
        gt=0,
        description="Number of hard context reset attempts before raising an error.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Hard reset retries",
                prominence=SettingProminence.MINOR,
                depends_on=("enabled",),
            ).model_dump()
        },
    )
    hard_context_reset_context_scaling: float = Field(
        default=0.8,
        gt=0.0,
        lt=1.0,
        description=(
            "Factor used to reduce event string size after a hard context reset "
            "summarization failure."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Hard reset scaling",
                prominence=SettingProminence.MINOR,
                depends_on=("enabled",),
            ).model_dump()
        },
    )

    def build_condenser(self, llm: LLM) -> LLMSummarizingCondenser | None:
        """Create a condenser from these settings, or ``None`` if disabled."""
        if not self.enabled:
            return None

        from openhands.sdk.context.condenser import LLMSummarizingCondenser

        condenser_llm = llm.model_copy(update={"usage_id": "condenser"})
        condenser_llm.reset_metrics()
        condenser_kwargs = self.model_dump(
            exclude={"enabled", "condenser_kind"},
            exclude_none=True,
        )
        return LLMSummarizingCondenser(llm=condenser_llm, **condenser_kwargs)


class NoOpCondenserSettings(CondenserSettings):
    """Settings for a condenser that leaves conversation views unchanged."""

    max_size: ClassVar[int] = 240  # type: ignore[reportIncompatibleVariableOverride]
    condenser_kind: Literal["no_op"] = Field(
        default="no_op",
        description=(
            "Discriminator for the condenser settings union. ``'no_op'`` selects "
            "a condenser that leaves conversation views unchanged."
        ),
    )

    def build_condenser(self, llm: LLM) -> CondenserBase | None:  # noqa: ARG002
        """Create a condenser from these settings, or ``None`` if disabled."""
        if not self.enabled:
            return None

        from openhands.sdk.context.condenser import NoOpCondenser

        return NoOpCondenser()


def _condenser_settings_discriminator(value: Any) -> str:
    """Discriminator for :data:`CondenserSettingsConfig`.

    Existing payloads predate ``condenser_kind`` and carried only the default
    LLM summarizing condenser fields. Treat missing discriminators as
    ``'llm_summarizing'`` so those payloads continue to validate.
    """
    if isinstance(value, BaseModel):
        return getattr(value, "condenser_kind", "llm_summarizing")
    if isinstance(value, dict):
        return value.get("condenser_kind", "llm_summarizing")
    return "llm_summarizing"


CondenserSettingsConfig = Annotated[
    Annotated[LLMSummarizingCondenserSettings, Tag("llm_summarizing")]
    | Annotated[NoOpCondenserSettings, Tag("no_op")],
    Discriminator(_condenser_settings_discriminator),
]
"""Discriminated union over the condenser-settings variants."""


class VerificationSettings(BaseModel):
    """Critic and iterative-refinement settings for the agent."""

    # -- Critic --
    critic_enabled: bool = Field(
        default=False,
        description="Enable critic evaluation for the agent.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Enable critic",
                prominence=SettingProminence.CRITICAL,
            ).model_dump()
        },
    )
    critic_mode: CriticMode = Field(
        default="finish_and_message",
        description="When critic evaluation should run.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic mode",
                prominence=SettingProminence.MINOR,
                depends_on=("critic_enabled",),
            ).model_dump()
        },
    )
    enable_iterative_refinement: bool = Field(
        default=False,
        description=(
            "Automatically retry tasks when critic scores fall below the threshold."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Enable iterative refinement",
                prominence=SettingProminence.CRITICAL,
                depends_on=("critic_enabled",),
            ).model_dump()
        },
    )
    critic_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Critic success threshold used for iterative refinement.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic threshold",
                prominence=SettingProminence.MINOR,
                depends_on=("critic_enabled", "enable_iterative_refinement"),
            ).model_dump()
        },
    )
    max_refinement_iterations: int = Field(
        default=3,
        ge=1,
        description="Maximum number of refinement attempts after critic feedback.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Max refinement iterations",
                prominence=SettingProminence.MINOR,
                depends_on=("critic_enabled", "enable_iterative_refinement"),
            ).model_dump()
        },
    )

    # -- Critic deployment --
    critic_server_url: str | None = Field(
        default=None,
        description=(
            "Override the critic service URL. "
            "When None, the APIBasedCritic default is used."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic server URL",
                prominence=SettingProminence.MINOR,
                depends_on=("critic_enabled",),
            ).model_dump()
        },
    )
    critic_model_name: str | None = Field(
        default=None,
        description=(
            "Override the critic model name. "
            "When None, the APIBasedCritic default is used."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic model name",
                prominence=SettingProminence.MINOR,
                depends_on=("critic_enabled",),
            ).model_dump()
        },
    )
    critic_api_key: str | SecretStr | None = Field(
        default=None,
        description=(
            "API key used to authenticate with the critic service. "
            "When None, the LLM's ``api_key`` is reused, which preserves "
            "the auto-configuration path for the All-Hands LLM proxy."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic API Key",
                prominence=SettingProminence.CRITICAL,
                depends_on=("critic_enabled",),
            ).model_dump()
        },
    )

    @field_validator("critic_api_key")
    @classmethod
    def _validate_critic_api_key(
        cls, v: str | SecretStr | None, info: ValidationInfo
    ) -> SecretStr | None:
        return validate_secret(v, info)

    @field_serializer("critic_api_key", when_used="always")
    def _serialize_critic_api_key(
        self, v: SecretStr | None, info: SerializationInfo
    ) -> Any:
        return serialize_secret(v, info)


def _default_llm_settings() -> LLM:
    model = LLM.model_fields["model"].get_default()
    assert isinstance(model, str)
    return LLM(model=model)


_RequestT = TypeVar("_RequestT")

AGENT_SETTINGS_SCHEMA_VERSION = 4
CONVERSATION_SETTINGS_SCHEMA_VERSION = 1


class AgentSettingsBase(BaseModel):
    """Shared base for all agent-settings variants.

    Provides the three pieces common to every variant:

    - :attr:`schema_version` — used for persisted-payload migrations.
    - :meth:`export_schema` — structured field description for UIs.
    - :meth:`create_agent` — canonical construction path; concrete subclasses
      must override this.

    The ``llm`` field is intentionally *not* hoisted here — its semantics
    differ between variants (execution config vs. attribution identity) and
    the metadata overrides would make a shared field awkward.

    Use :data:`AgentSettingsConfig` as the type for fields that may hold
    either the :class:`OpenHandsAgentSettings` or :class:`ACPAgentSettings`
    variant. Use :func:`validate_agent_settings` to validate raw payloads.
    """

    schema_version: int = Field(default=AGENT_SETTINGS_SCHEMA_VERSION, ge=1)

    @classmethod
    def export_schema(cls) -> SettingsSchema:
        """Export a structured schema describing configurable settings."""
        return export_settings_schema(cls)

    def create_agent(self) -> AgentBase:
        """Build an agent from these settings.

        Subclasses (:class:`OpenHandsAgentSettings`, :class:`ACPAgentSettings`)
        override this to return the appropriate
        :class:`~openhands.sdk.agent.base.AgentBase` subclass.
        Calling this on the base class directly raises :exc:`NotImplementedError`.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement create_agent()"
        )


PersistedSettingsMigrator = Callable[[dict[str, Any]], dict[str, Any]]


def _copy_persisted_payload(data: Any) -> dict[str, Any]:
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
        if not isinstance(payload, dict):
            raise TypeError("Persisted settings payload must serialize to a mapping.")
        return payload
    if isinstance(data, Mapping):
        return dict(data)
    raise TypeError("Persisted settings payload must be a mapping or BaseModel.")


def _apply_persisted_migrations(
    data: Any,
    *,
    current_version: int,
    migrations: dict[int, PersistedSettingsMigrator],
    payload_name: str,
) -> dict[str, Any]:
    payload = _copy_persisted_payload(data)
    version_raw = payload.get("schema_version", 0)
    if version_raw is None:
        version = 0
    elif isinstance(version_raw, int) and not isinstance(version_raw, bool):
        version = version_raw
    else:
        raise TypeError(
            f"{payload_name} schema_version must be an integer, got "
            f"{type(version_raw).__name__}."
        )

    if version < 0:
        raise ValueError(f"{payload_name} schema_version must be non-negative.")
    if version > current_version:
        raise ValueError(
            f"{payload_name} schema_version {version} is newer than supported "
            f"version {current_version}."
        )

    while version < current_version:
        migrate = migrations.get(version)
        if migrate is None:
            raise ValueError(
                f"No migration registered for {payload_name} schema_version {version}."
            )
        payload = migrate(dict(payload))
        next_version = payload.get("schema_version")
        if not isinstance(next_version, int) or isinstance(next_version, bool):
            raise ValueError(
                f"Migration for {payload_name} schema_version {version} did not "
                "produce a valid integer schema_version."
            )
        if next_version <= version:
            raise ValueError(
                f"Migration for {payload_name} schema_version {version} did not "
                "advance the schema_version."
            )
        version = next_version

    return payload


def _migrate_agent_settings_v0_to_v1(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    migrated["schema_version"] = 1
    migrated.setdefault("agent_kind", _agent_settings_discriminator(migrated))
    return migrated


def _migrate_agent_settings_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize the deprecated ``agent_kind: 'llm'`` discriminator to
    ``'openhands'``.

    Before the v1.19.0 ``LLMAgentSettings`` → ``OpenHandsAgentSettings`` rename,
    persisted payloads carried ``agent_kind: 'llm'``. The two classes are
    field-compatible (``LLMAgentSettings`` is a subclass of
    ``OpenHandsAgentSettings`` that only narrows the discriminator literal),
    and ``LLMAgentSettings``'s import aliases were removed in v1.24.0. Rewriting
    the discriminator on read lets callers that explicitly validate as
    ``OpenHandsAgentSettings`` (the canonical class) accept legacy data
    without losing any fields.
    """
    migrated = dict(payload)
    migrated["schema_version"] = 2
    if migrated.get("agent_kind") == "llm":
        migrated["agent_kind"] = "openhands"
    return migrated


def _migrate_agent_settings_v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop deprecated verification fields moved to ``ConversationSettings``."""
    migrated = dict(payload)
    verification = migrated.get("verification")
    if isinstance(verification, Mapping):
        verification = dict(verification)
        verification.pop("confirmation_mode", None)
        verification.pop("security_analyzer", None)
        migrated["verification"] = verification
    migrated["schema_version"] = 3
    return migrated


def _migrate_agent_settings_v3_to_v4(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    llm = migrated.get("llm")
    if isinstance(llm, dict):
        migrated["llm"] = canonicalize_openhands_llm_payload(llm)
    migrated["schema_version"] = 4
    return migrated


def _migrate_conversation_settings_v0_to_v1(
    payload: dict[str, Any],
) -> dict[str, Any]:
    migrated = dict(payload)
    migrated["schema_version"] = 1
    return migrated


_AGENT_SETTINGS_MIGRATIONS: dict[int, PersistedSettingsMigrator] = {
    0: _migrate_agent_settings_v0_to_v1,
    1: _migrate_agent_settings_v1_to_v2,
    2: _migrate_agent_settings_v2_to_v3,
    3: _migrate_agent_settings_v3_to_v4,
}
_CONVERSATION_SETTINGS_MIGRATIONS: dict[int, PersistedSettingsMigrator] = {
    0: _migrate_conversation_settings_v0_to_v1,
}


class ConversationSettings(BaseModel):
    schema_version: int = Field(default=CONVERSATION_SETTINGS_SCHEMA_VERSION, ge=1)

    # --- runtime fields (populated on-the-fly, not persisted) ---------------
    agent_settings: AgentSettingsConfig | None = Field(
        default=None,
        exclude=True,
        description=(
            "Agent settings used to build the Agent for the conversation. "
            "When set, create_request() will automatically build the agent "
            "and populate secrets from agent_context. Accepts either the "
            "``OpenHandsAgentSettings`` or ``ACPAgentSettings`` variant."
        ),
    )
    workspace: LocalWorkspace | None = Field(
        default=None,
        exclude=True,
        description="Working directory for the conversation.",
    )
    conversation_id: UUID | None = Field(
        default=None,
        exclude=True,
        description="Conversation UUID. Auto-generated if not set.",
    )
    initial_message: SendMessageRequest | None = Field(
        default=None,
        exclude=True,
        description="Initial message to send to the agent.",
    )
    tool_module_qualnames: dict[str, str] = Field(
        default_factory=dict,
        exclude=True,
        description="Mapping of tool names to module qualnames.",
    )
    agent_definitions: list[AgentDefinition] = Field(
        default_factory=list,
        exclude=True,
        description="Agent definitions for task tools.",
    )
    plugins: list[PluginSource] | None = Field(
        default=None,
        exclude=True,
        description="Plugin sources to load for this conversation.",
    )
    hook_config: HookConfig | None = Field(
        default=None,
        exclude=True,
        description="Hook configuration for lifecycle events.",
    )
    selected_repository: str | None = Field(
        default=None,
        exclude=True,
        description="Repository selected for the conversation.",
    )
    observability_metadata: ConversationObservabilityMetadata | None = Field(
        default=None,
        exclude=True,
        description="Trace-level metadata for observability backends.",
    )
    observability_tags: ConversationObservabilityTags | None = Field(
        default=None,
        exclude=True,
        description="Tags for the conversation root observability span.",
    )

    # --- persisted fields ---------------------------------------------------
    max_iterations: int = Field(
        default=500,
        ge=1,
        description=(
            "Maximum number of iterations the conversation will run before stopping."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Max iterations",
                prominence=SettingProminence.MAJOR,
            ).model_dump()
        },
    )
    confirmation_mode: bool = Field(
        default=False,
        description="Require user confirmation before executing risky actions.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Confirmation mode",
                prominence=SettingProminence.CRITICAL,
            ).model_dump(),
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="verification",
                label="Verification",
            ).model_dump(),
        },
    )
    security_analyzer: SecurityAnalyzerType | None = Field(
        default="llm",
        description="Security analyzer that evaluates actions before execution.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Security analyzer",
                prominence=SettingProminence.MAJOR,
                depends_on=("confirmation_mode",),
            ).model_dump(),
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="verification",
                label="Verification",
            ).model_dump(),
        },
    )

    @classmethod
    def export_schema(cls) -> SettingsSchema:
        """Export a structured schema describing configurable conversation settings."""
        return export_settings_schema(cls)

    @classmethod
    def from_persisted(cls, data: Any) -> ConversationSettings:
        """Load persisted conversation settings, applying any schema migrations."""
        payload = _apply_persisted_migrations(
            data,
            current_version=CONVERSATION_SETTINGS_SCHEMA_VERSION,
            migrations=_CONVERSATION_SETTINGS_MIGRATIONS,
            payload_name="ConversationSettings",
        )
        return cls.model_validate(payload)

    def _build_confirmation_policy(self):
        from openhands.sdk.security.confirmation_policy import (
            AlwaysConfirm,
            ConfirmRisky,
            NeverConfirm,
        )

        if not self.confirmation_mode:
            return NeverConfirm()
        if (self.security_analyzer or "").lower() == "llm":
            return ConfirmRisky()
        return AlwaysConfirm()

    def _build_security_analyzer(self):
        analyzer_kind = (self.security_analyzer or "").lower()
        if not analyzer_kind or analyzer_kind == "none":
            return None
        if analyzer_kind == "llm":
            from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer

            return LLMSecurityAnalyzer()
        return None

    def _start_request_kwargs(self, **kwargs: Any) -> dict[str, Any]:
        payload = dict(kwargs)

        # --- agent (from agent_settings) ------------------------------------
        # Both settings variants expose a .create_agent() method; the LLM
        # variant returns an ``Agent`` and the ACP variant returns an
        # ``ACPAgent``. Callers that want a narrowed type should access
        # ``self.agent_settings.create_agent()`` directly.
        if "agent" not in payload and self.agent_settings is not None:
            payload["agent"] = self.agent_settings.create_agent()

        # --- secrets (from agent's context) ---------------------------------
        # ACPAgent may carry prompt-only context, but its execution context is
        # owned by the subprocess. ``getattr(..., None)`` keeps this no-op for
        # agents without AgentContext.
        agent = payload.get("agent")
        if "secrets" not in payload and agent is not None:
            ctx = getattr(agent, "agent_context", None)
            if ctx is not None and getattr(ctx, "secrets", None):
                payload["secrets"] = ctx.secrets

        # --- runtime fields -------------------------------------------------
        if self.workspace is not None:
            payload.setdefault("workspace", self.workspace)
        if self.conversation_id is not None:
            payload.setdefault("conversation_id", self.conversation_id)
        if self.initial_message is not None:
            payload.setdefault("initial_message", self.initial_message)
        if self.tool_module_qualnames:
            payload.setdefault("tool_module_qualnames", self.tool_module_qualnames)
        if self.agent_definitions:
            payload.setdefault("agent_definitions", self.agent_definitions)
        if self.plugins is not None:
            payload.setdefault("plugins", self.plugins)
        if self.hook_config is not None:
            payload.setdefault("hook_config", self.hook_config)
        if self.observability_metadata is not None:
            payload.setdefault("observability_metadata", self.observability_metadata)
        if self.observability_tags is not None:
            payload.setdefault("observability_tags", self.observability_tags)

        # --- persisted defaults ---------------------------------------------
        payload.setdefault("confirmation_policy", self._build_confirmation_policy())
        payload.setdefault("security_analyzer", self._build_security_analyzer())
        payload.setdefault("max_iterations", self.max_iterations)
        return payload

    def create_request(
        self,
        request_type: Callable[..., _RequestT],
        /,
        **kwargs: Any,
    ) -> _RequestT:
        """Build a request from these settings.

        Every field on ``ConversationSettings`` is used as a default.
        Explicit *kwargs* override any setting.
        """
        return request_type(**self._start_request_kwargs(**kwargs))


AgentKind = Literal["openhands", "llm", "acp"]

ACPServerKind = Literal["claude-code", "codex", "gemini-cli", "custom"]
"""Known ACP backend servers the GUI can pick from.

``custom`` means the user supplies the raw ``acp_command`` themselves;
the other choices map to a default npx command stored in
:data:`~openhands.sdk.settings.acp_providers.ACP_PROVIDERS`.
"""


class OpenHandsAgentSettings(AgentSettingsBase):
    """Settings for a standard LLM-backed :class:`Agent`.

    This is the long-standing ``AgentSettings`` shape; fields here build
    the default ``Agent`` (LLM + tools + MCP + condenser + critic).
    """

    agent_kind: Literal["openhands"] = Field(
        default="openhands",
        description=(
            "Discriminator for the ``AgentSettings`` union. ``'openhands'`` selects "
            "the standard built-in OpenHands agent."
        ),
    )
    agent: str = Field(
        default="CodeActAgent",
        description="Agent class to use.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Agent",
                prominence=SettingProminence.MAJOR,
                variant="openhands",
            ).model_dump()
        },
    )
    llm: LLM = Field(
        default_factory=_default_llm_settings,
        description="LLM settings for the agent.",
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="llm",
                label="LLM",
                variant="openhands",
            ).model_dump()
        },
    )
    tools: list[Tool] = Field(
        default_factory=list,
        description="Tools available to the agent.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Tools",
                prominence=SettingProminence.MAJOR,
                variant="openhands",
            ).model_dump()
        },
    )
    enable_sub_agents: bool = Field(
        default=False,
        description="Enable sub-agent delegation via TaskToolSet.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Enable sub-agents",
                prominence=SettingProminence.MAJOR,
                variant="openhands",
            ).model_dump()
        },
    )
    enable_switch_llm_tool: bool = Field(
        default=True,
        description=(
            "Enable the built-in switch_llm tool for switching between saved "
            "LLM profiles."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Enable LLM switching tool",
                prominence=SettingProminence.MINOR,
                variant="openhands",
            ).model_dump()
        },
    )
    tool_concurrency_limit: int = Field(
        default=1,
        ge=1,
        description=(
            "Maximum number of tool calls to execute concurrently per agent step. "
            "1 = sequential (default). Values > 1 enable parallel tool calls; "
            "concurrent tools share the conversation object, filesystem, and "
            "working directory, so mutations to shared state may race."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Parallel tool calls",
                prominence=SettingProminence.MAJOR,
                variant="openhands",
            ).model_dump()
        },
    )

    mcp_config: MCPConfig | None = Field(
        default=None,
        description="MCP server configuration for the agent.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="MCP configuration",
                prominence=SettingProminence.MINOR,
                variant="openhands",
            ).model_dump()
        },
    )
    agent_context: AgentContext = Field(
        default_factory=AgentContext,
        description="Context for the agent (skills, secrets, message suffixes).",
    )
    condenser: CondenserSettingsConfig = Field(
        default_factory=LLMSummarizingCondenserSettings,
        description="Condenser settings for the agent.",
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="condenser",
                label="Condenser",
                variant="openhands",
            ).model_dump()
        },
    )
    verification: VerificationSettings = Field(
        default_factory=VerificationSettings,
        description="Verification settings for the agent critic.",
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="verification",
                label="Verification",
                variant="openhands",
            ).model_dump()
        },
    )

    # ``mcp_config`` (de)serialization is shared with ACPAgentSettings via the
    # module-level helpers — these stubs just bind them to the field.
    @field_validator("condenser", mode="before")
    @classmethod
    def _upgrade_base_condenser_settings(cls, value: Any) -> Any:
        if type(value) is CondenserSettings:
            return LLMSummarizingCondenserSettings.model_validate(value.model_dump())
        return value

    @field_validator("mcp_config", mode="before")
    @classmethod
    def _normalize_empty_mcp_config(cls, value: Any) -> Any:
        return normalize_empty_mcp_config(value)

    @field_validator("mcp_config", mode="before")
    @classmethod
    def _decrypt_mcp_secret_values(cls, value: Any, info: ValidationInfo) -> Any:
        return decrypt_mcp_config_secrets(value, info)

    @field_serializer("mcp_config")
    def _serialize_mcp_config(
        self, value: MCPConfig | None, info: SerializationInfo
    ) -> dict[str, Any]:
        return serialize_mcp_config(value, info)

    def create_agent(self) -> Agent:
        """Build an :class:`Agent` purely from these settings.

        Example::

            settings = OpenHandsAgentSettings(
                llm=LLM(model="m", api_key="k"),
                tools=[Tool(name="TerminalTool")],
            )
            agent = settings.create_agent()
        """
        from openhands.sdk.agent import Agent
        from openhands.sdk.llm.auth.openai import create_subscription_llm_from_config
        from openhands.sdk.tool.builtins import BUILT_IN_TOOLS, SwitchLLMTool

        # Bypass ``_serialize_mcp_config``: MCP servers need real env/headers.
        mcp_config = (
            self.mcp_config.model_dump(exclude_none=True, exclude_defaults=True)
            if self.mcp_config is not None
            else {}
        )
        include_default_tools = [tool.__name__ for tool in BUILT_IN_TOOLS]
        if self.enable_switch_llm_tool:
            include_default_tools.append(SwitchLLMTool.__name__)

        llm = create_subscription_llm_from_config(self.llm)
        condenser = None if llm.is_subscription else self.build_condenser(llm)
        return Agent(
            llm=llm,
            tools=self.tools,
            mcp_config=mcp_config,
            include_default_tools=include_default_tools,
            agent_context=self.agent_context,
            condenser=condenser,
            critic=self.build_critic(),
            tool_concurrency_limit=self.tool_concurrency_limit,
        )

    def build_condenser(self, llm: LLM) -> CondenserBase | None:
        """Create a condenser from these settings, or ``None`` if disabled."""
        return self.condenser.build_condenser(llm)

    def build_critic(self) -> CriticBase | None:
        """Create an :class:`APIBasedCritic` from these settings.

        Returns ``None`` when the critic is disabled or when no API key
        is available (the critic service requires authentication).

        If ``verification.critic_api_key`` is set it is used to
        authenticate with the critic service; otherwise the LLM's
        ``api_key`` is reused. This preserves the existing
        auto-configuration path for the All-Hands LLM proxy while
        letting deployments route the critic through a different
        provider (e.g. an LLM proxy with its own credential).

        If ``verification.critic_server_url`` or
        ``verification.critic_model_name`` are set they override the
        ``APIBasedCritic`` defaults, allowing deployments to route
        through a custom endpoint (e.g. an LLM proxy).
        """
        if not self.verification.critic_enabled:
            return None

        api_key = self.verification.critic_api_key or self.llm.api_key
        if api_key is None:
            return None

        from openhands.sdk.critic.base import IterativeRefinementConfig
        from openhands.sdk.critic.impl.api import APIBasedCritic

        iterative_refinement = None
        if self.verification.enable_iterative_refinement:
            iterative_refinement = IterativeRefinementConfig(
                success_threshold=self.verification.critic_threshold,
                max_iterations=self.verification.max_refinement_iterations,
            )

        overrides: dict[str, Any] = {}
        if self.verification.critic_server_url is not None:
            overrides["server_url"] = self.verification.critic_server_url
        if self.verification.critic_model_name is not None:
            overrides["model_name"] = self.verification.critic_model_name

        return APIBasedCritic(
            api_key=api_key,
            mode=self.verification.critic_mode,
            iterative_refinement=iterative_refinement,
            **overrides,
        )


class ACPAgentSettings(AgentSettingsBase):
    """Settings for an ACP (Agent Client Protocol) agent.

    ``create_agent()`` returns an :class:`ACPAgent` that delegates to a
    subprocess ACP server.  The ACP server manages its own system prompt,
    tools, MCP, and (primary) LLM calls; those fields from
    :class:`OpenHandsAgentSettings` do not apply here.

    The :attr:`llm` field is deprecated (removed in 1.33.0). ``ACPAgent``
    uses it purely for cost/token attribution, never for LLM requests;
    :attr:`acp_model` is the model identity. Credentials set on it
    (``llm.api_key`` / ``llm.base_url``) are ignored — provider credentials
    ride the conversation secrets channel (``request.secrets`` /
    ``agent_context.secrets`` → ``state.secret_registry``) keyed by the
    provider's env var name (:attr:`api_key_env_var`).
    """

    agent_kind: Literal["acp"] = Field(
        default="acp",
        description=(
            "Discriminator for the ``AgentSettings`` union. ``'acp'`` selects "
            "an ACP-delegating agent."
        ),
    )
    acp_server: ACPServerKind = Field(
        default="claude-code",
        description=(
            "Which ACP-compatible backend to launch. Each choice maps to a "
            "default subprocess command (see ``acp_command`` to override)."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="ACP server",
                prominence=SettingProminence.CRITICAL,
            ).model_dump(),
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="acp",
                label="ACP (Agent Client Protocol)",
                variant="acp",
            ).model_dump(),
        },
    )
    acp_command: list[str] = Field(
        default_factory=list,
        description=(
            "Optional explicit command to launch the ACP subprocess. Leave "
            "empty to use the default for :attr:`acp_server` (e.g. ``npx -y "
            "@agentclientprotocol/claude-agent-acp`` for ``claude-code``). "
            "Must be set when :attr:`acp_server` is ``'custom'``."
        ),
        json_schema_extra={
            # Deliberately no ``depends_on=("acp_server",)``: the frontend's
            # ``depends_on`` filter does a boolean check, which would evaluate
            # to false for the string-valued ``acp_server`` and hide the
            # field outright. Users see ``acp_command`` in the "all" view of
            # the ACP Server page if they need to supply a custom command.
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="ACP command (custom override)",
                prominence=SettingProminence.MINOR,
            ).model_dump(),
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="acp",
                label="ACP (Agent Client Protocol)",
                variant="acp",
            ).model_dump(),
        },
    )
    acp_args: list[str] = Field(
        default_factory=list,
        description="Additional arguments appended to the ACP server command.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="ACP extra args",
                prominence=SettingProminence.MINOR,
            ).model_dump(),
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="acp",
                label="ACP (Agent Client Protocol)",
                variant="acp",
            ).model_dump(),
        },
    )
    acp_model: str | None = Field(
        default=None,
        description=(
            "Model identifier for the ACP server to use (e.g. ``'sonnet'`` or "
            "``'gpt-5.5'``). Applied via the protocol — set_config_option(model) "
            "for configOptions-based servers (codex, claude), else "
            "set_session_model. Leave blank to let the server pick its default."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="ACP model",
                prominence=SettingProminence.CRITICAL,
            ).model_dump(),
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="acp",
                label="ACP (Agent Client Protocol)",
                variant="acp",
            ).model_dump(),
        },
    )
    acp_session_mode: str | None = Field(
        default=None,
        description=(
            "Session mode ID (e.g. ``bypassPermissions``). Leave blank to "
            "auto-detect from the ACP server type."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="ACP session mode",
                prominence=SettingProminence.MINOR,
            ).model_dump(),
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="acp",
                label="ACP (Agent Client Protocol)",
                variant="acp",
            ).model_dump(),
        },
    )
    acp_prompt_timeout: float = Field(
        default=1800.0,
        gt=0,
        description=(
            "Inactivity timeout (seconds) for a single ACP prompt() round-trip. "
            "The deadline resets on every update from the ACP server, so a "
            "steadily-progressing agent keeps running; the prompt is only "
            "aborted after this many seconds with no activity at all."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="ACP prompt inactivity timeout (seconds)",
                prominence=SettingProminence.MINOR,
            ).model_dump(),
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="acp",
                label="ACP (Agent Client Protocol)",
                variant="acp",
            ).model_dump(),
        },
    )
    mcp_config: MCPConfig | None = Field(
        default=None,
        description=(
            "MCP servers to make available to the ACP subprocess. Unlike the "
            "OpenHands agent — where these become in-process MCP tools — the "
            "servers are forwarded to the ACP server at session creation and it "
            "owns the connection. Remote (http/sse) servers are only forwarded "
            "when the ACP server advertises support for that transport; stdio "
            "servers (which run inside the runtime) are always forwarded."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="MCP configuration",
                prominence=SettingProminence.MINOR,
                variant="acp",
            ).model_dump(),
        },
    )

    # Same shared ``mcp_config`` (de)serialization as OpenHandsAgentSettings.
    @field_validator("mcp_config", mode="before")
    @classmethod
    def _normalize_empty_mcp_config(cls, value: Any) -> Any:
        return normalize_empty_mcp_config(value)

    @field_validator("mcp_config", mode="before")
    @classmethod
    def _decrypt_mcp_secret_values(cls, value: Any, info: ValidationInfo) -> Any:
        return decrypt_mcp_config_secrets(value, info)

    @field_serializer("mcp_config")
    def _serialize_mcp_config(
        self, value: MCPConfig | None, info: SerializationInfo
    ) -> dict[str, Any]:
        return serialize_mcp_config(value, info)

    # Programmatic / downstream-facing knob, deliberately NOT surfaced in the
    # settings-form UI (no SETTINGS_METADATA_KEY): the deploying application sets
    # it (e.g. when conversations share a sandbox under grouping), not the end
    # user. See ``ACPAgent.acp_isolate_data_dir`` for the full rationale.
    acp_isolate_data_dir: bool = Field(
        default=False,
        description=(
            "Give the ACP subprocess a per-conversation CLI data/config root "
            "instead of the shared user HOME. Forwarded to "
            ":attr:`~openhands.sdk.agent.ACPAgent.acp_isolate_data_dir`; off by "
            "default. Enable from the deploying application when several "
            "conversations share one sandbox (see #1019)."
        ),
    )
    # Programmatic / downstream-facing knob, deliberately NOT surfaced in the
    # settings-form UI (no SETTINGS_METADATA_KEY): it's a list of structured
    # specs a downstream application supplies in code to support other ACP CLIs,
    # not an end-user field. The built-in providers work via the default.
    acp_file_secrets: list[ACPFileSecretSpec] = Field(
        default_factory=lambda: list(default_acp_file_secrets()),
        description=(
            "Reserved 'file-content' credential secrets the SDK materialises to "
            "disk before launching the ACP subprocess (e.g. Codex auth.json, "
            "Gemini Vertex SA JSON). Defaults to the built-in supported "
            "providers; override to support other ACP servers with different "
            "file-auth schemes."
        ),
    )
    llm: LLM = Field(
        default_factory=_default_llm_settings,
        description=(
            "DEPRECATED (removed in 1.33.0): LLM identity used for cost/token "
            "attribution. The ACP subprocess makes its own model calls; "
            "``acp_model`` is the model identity. Credentials set here "
            "(``api_key`` / ``base_url``) are ignored — route provider "
            "credentials through the conversation secrets channel "
            "(agent_context.secrets / StartConversationRequest.secrets, which "
            "route through state.secret_registry), keyed by the provider's "
            "env var name."
        ),
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="llm",
                label="LLM (for metrics)",
                variant="acp",
            ).model_dump()
        },
    )
    agent_context: AgentContext | None = Field(
        default=None,
        description=(
            "Prompt-only context for the ACP server. ``secrets`` here are "
            "advertised to the agent (names/descriptions) and reach the "
            "subprocess env through ``state.secret_registry``: "
            "``LocalConversation`` seeds ``agent_context.secrets`` into the "
            "registry at conversation init (below ``request.secrets``), so "
            "callers that build the request outside Python (e.g. canvas-local) "
            "are covered too, not just the ``create_request`` path. Provider "
            "credentials belong here (or in ``request.secrets``) keyed by the "
            "provider's env var name."
        ),
    )

    @property
    def provider_info(self) -> ACPProviderInfo | None:
        """Registry entry for :attr:`acp_server`, or ``None`` for ``'custom'``."""
        return get_acp_provider(self.acp_server)

    @property
    def api_key_env_var(self) -> str | None:
        """Env var name the ACP subprocess expects for its API key.

        Delegates to the :data:`~openhands.sdk.settings.acp_providers.ACP_PROVIDERS`
        registry.  Returns ``None`` for ``'custom'`` servers — users manage
        credentials entirely via the conversation secrets channel in that case.
        """
        info = self.provider_info
        return info.api_key_env_var if info is not None else None

    @property
    def base_url_env_var(self) -> str | None:
        """Env var for proxy/base-URL routing, or ``None`` if unsupported.

        Delegates to the :data:`~openhands.sdk.settings.acp_providers.ACP_PROVIDERS`
        registry.
        """
        info = self.provider_info
        return info.base_url_env_var if info is not None else None

    def resolve_provider_env(self) -> dict[str, str]:
        """Derive provider-native env vars from the attribution LLM settings.

        Built-in ACP providers read credentials and optional base URLs from
        provider-specific env var names. This helper translates the generic
        :attr:`llm` settings into that provider-native subprocess environment.
        Custom servers return an empty mapping.

        .. deprecated:: 1.28.0
            Removed in 1.33.0 together with :attr:`llm`. ``create_agent()``
            no longer reads ``llm.api_key`` / ``llm.base_url``; supply
            provider credentials as conversation secrets keyed by
            :attr:`api_key_env_var` (e.g. ``ANTHROPIC_API_KEY``) instead.
        """
        warn_deprecated(
            "ACPAgentSettings.resolve_provider_env",
            deprecated_in="1.28.0",
            removed_in="1.33.0",
            details=(
                "Supply ACP provider credentials as conversation secrets "
                "(agent_context.secrets / StartConversationRequest.secrets, "
                "which route through state.secret_registry) keyed by the "
                "provider's env var name instead of ACPAgentSettings.llm."
            ),
        )
        env: dict[str, str] = {}

        api_key = self.llm.api_key
        if api_key is not None and self.api_key_env_var:
            key_value = (
                api_key.get_secret_value()
                if isinstance(api_key, SecretStr)
                else str(api_key)
            )
            key_value = key_value.strip()
            if key_value:
                env[self.api_key_env_var] = key_value

        base_url = self.llm.base_url
        if base_url is not None and self.base_url_env_var:
            base_url_value = str(base_url).strip()
            if base_url_value:
                env[self.base_url_env_var] = base_url_value

        return env

    def resolve_acp_command(self) -> list[str]:
        """Return the effective subprocess command for this settings block.

        Uses :attr:`acp_command` verbatim when non-empty; otherwise looks
        up the default from :data:`~openhands.sdk.settings.acp_providers.ACP_PROVIDERS`.
        Raises ``ValueError`` when :attr:`acp_server` is ``'custom'`` but
        no explicit command is set (there is no sensible default to fall back to).

        The result is routed through :meth:`_prefer_pinned_binary`, which swaps
        an ``npx`` command for the pinned binary when it is on ``PATH`` (a no-op
        otherwise).
        """
        if self.acp_command:
            command = list(self.acp_command)
        elif self.acp_server == "custom":
            raise ValueError(
                "ACPAgentSettings.acp_command must be set when "
                "acp_server='custom' — there is no default to fall back to"
            )
        else:
            info = get_acp_provider(self.acp_server)
            if info is None:
                raise ValueError(
                    f"No default ACP command for acp_server={self.acp_server!r}"
                )
            command = list(info.default_command)
        return self._prefer_pinned_binary(command)

    @staticmethod
    def _parse_npx_invocation(command: Sequence[str]) -> tuple[str, list[str]] | None:
        """Parse an ``npx``-style launch command into ``(package, extra_args)``.

        ``["npx", "-y", "@scope/pkg", "--flag"]`` → ``("@scope/pkg", ["--flag"])``,
        skipping any leading ``npx`` flags such as ``-y`` / ``--yes``. Returns
        ``None`` when *command* is not an ``npx`` invocation (e.g. an
        already-resolved binary path), so callers leave it untouched.
        """
        if not command or command[0] != "npx":
            return None
        # Drop leading npx flags (-y, --yes, ...) to find the package name.
        rest = list(itertools.dropwhile(lambda arg: arg.startswith("-"), command[1:]))
        if not rest:
            return None
        package, *extra_args = rest
        return package, extra_args

    @staticmethod
    def _npm_package_name(package: str) -> str:
        """Strip any ``@version`` specifier from an npm package spec.

        ``@scope/pkg@1.2.3`` → ``@scope/pkg``; ``pkg@1.2.3`` → ``pkg``; an
        unversioned spec is returned unchanged. The version separator is the
        ``@`` *after* the name — for scoped specs that is the second ``@`` (the
        first introduces the scope), so a leading ``@`` is skipped.
        """
        start = 1 if package.startswith("@") else 0
        at = package.find("@", start)
        return package[:at] if at != -1 else package

    def _prefer_pinned_binary(self, command: list[str]) -> list[str]:
        """Swap an ``npx -y <pkg>`` command for the provider's pinned binary.

        When *command* is an ``npx`` invocation of this provider's package and
        the provider's ``binary_name`` resolves via :func:`shutil.which`, return
        ``[binary_name, *extra]`` (preserving trailing args like gemini's
        ``--acp``) — running the agent-server image's pinned wrapper instead of
        downloading npm-latest. Returned unchanged otherwise: no pinned binary
        (custom server), a non-matching/non-npx command, or the binary not on
        ``PATH`` (local dev).

        Package matching ignores any ``@version`` suffix: the registry default
        is version-pinned (so the native fallback can't drift to npm ``latest``),
        but a client may still send the bare or a differently-pinned package
        name. In the image the pinned binary stands in for the provider's package
        regardless of the requested version, so the rewrite compares names only.
        """
        info = get_acp_provider(self.acp_server)
        if info is None or info.binary_name is None:
            return command

        default_parsed = self._parse_npx_invocation(info.default_command)
        actual_parsed = self._parse_npx_invocation(command)
        if default_parsed is None or actual_parsed is None:
            return command

        default_pkg, _ = default_parsed
        actual_pkg, extra = actual_parsed
        same_package = self._npm_package_name(actual_pkg) == self._npm_package_name(
            default_pkg
        )
        if not same_package or shutil.which(info.binary_name) is None:
            return command

        return [info.binary_name, *extra]

    def create_agent(self) -> ACPAgent:
        """Build an :class:`ACPAgent` from these settings.

        The subprocess command is resolved via :meth:`resolve_acp_command`
        which maps :attr:`acp_server` to a default when no explicit
        :attr:`acp_command` is set.

        Credentials on :attr:`llm` (``api_key`` / ``base_url``) are ignored:
        provider credentials ride the conversation secrets channel
        (``agent_context.secrets`` / ``StartConversationRequest.secrets``,
        which route through ``state.secret_registry``) keyed by the
        provider's env var name (:attr:`api_key_env_var`), exactly like the
        regular agent's credentials, and reach the subprocess from the
        registry.
        """
        from openhands.sdk.agent import ACPAgent

        # llm.api_key/base_url used to be folded into agent_context.secrets
        # here. Both production clients route credentials via request.secrets
        # already (canvas never sends llm; OpenHands strips the credentials
        # before create_agent), so the fold only fired for direct-SDK callers
        # — warn them toward the secrets channel instead of silently leaking
        # a profile base_url into the subprocess (#3632).
        if self.llm.api_key is not None or self.llm.base_url is not None:
            warn_deprecated(
                "ACPAgentSettings.llm",
                deprecated_in="1.28.0",
                removed_in="1.33.0",
                details=(
                    "create_agent() ignores its api_key/base_url. Supply ACP "
                    "provider credentials as conversation secrets "
                    "(agent_context.secrets / StartConversationRequest."
                    "secrets, which route through state.secret_registry) "
                    "keyed by the provider's env var name (e.g. "
                    "ANTHROPIC_API_KEY); use acp_model for model identity."
                ),
            )

        # Bypass ``_serialize_mcp_config``: the subprocess needs real
        # env/headers, not the masked/encrypted on-disk form.
        mcp_config = (
            self.mcp_config.model_dump(exclude_none=True, exclude_defaults=True)
            if self.mcp_config is not None
            else {}
        )

        return ACPAgent(
            llm=self.llm,
            acp_command=self.resolve_acp_command(),
            # Carry the authoritative provider key onto the agent: acp_command
            # alone does not reliably reverse-map to a provider, so consumers
            # (e.g. the conversation UI resolving a brand label / model list)
            # read it from ConversationInfo.agent.acp_server.
            acp_server=self.acp_server,
            acp_args=list(self.acp_args),
            acp_model=self.acp_model,
            acp_session_mode=self.acp_session_mode,
            acp_prompt_timeout=self.acp_prompt_timeout,
            acp_isolate_data_dir=self.acp_isolate_data_dir,
            acp_file_secrets=list(self.acp_file_secrets),
            agent_context=self.agent_context,
            mcp_config=mcp_config,
        )


class LLMAgentSettings(OpenHandsAgentSettings):
    """Legacy ``agent_kind='llm'`` variant of :class:`OpenHandsAgentSettings`.

    ``LLMAgentSettings`` was the public class name before the v1.19.0 rename.
    The public import aliases (``from openhands.sdk import LLMAgentSettings`` and
    ``from openhands.sdk.settings import LLMAgentSettings``) were removed in
    v1.24.0 — use :class:`OpenHandsAgentSettings` for all new code.

    The class itself is retained (reachable at
    ``openhands.sdk.settings.model.LLMAgentSettings``) because it remains a
    member of the settings discriminated union: it keeps ``agent_kind='llm'`` so
    persisted legacy payloads still deserialize and the API-breakage checker
    sees no field-value change versus the published release.
    """

    # Keep agent_kind as Literal["llm"] so the API-breakage checker sees no
    # field-value change compared with the PyPI release (which had this class
    # as the primary class with agent_kind="llm").  The discriminated union
    # routes "llm" payloads here; validate_agent_settings({}) still defaults
    # to OpenHandsAgentSettings ("openhands").
    agent_kind: Literal["llm"] = Field(  # type: ignore[assignment]
        default="llm",
        description=(
            "Discriminator for the ``AgentSettings`` union. ``'llm'`` selects "
            "the standard LLM-backed agent. Deprecated; use ``'openhands'``."
        ),
    )


def _agent_settings_discriminator(value: Any) -> str:
    """Discriminator for :data:`AgentSettingsConfig` — defaults to ``'openhands'``.

    Existing persisted payloads predate ``agent_kind`` and carry only
    OpenHands-agent fields. Treating a missing discriminator as ``'openhands'``
    lets those payloads validate without a migration.

    ``'llm'`` is still a valid tag, routed to the deprecated
    :class:`LLMAgentSettings` subclass.
    """
    if isinstance(value, BaseModel):
        return getattr(value, "agent_kind", "openhands")
    if isinstance(value, dict):
        return value.get("agent_kind", "openhands")
    return "openhands"


AgentSettingsConfig = Annotated[
    Annotated[OpenHandsAgentSettings, Tag("openhands")]
    | Annotated[LLMAgentSettings, Tag("llm")]
    | Annotated[ACPAgentSettings, Tag("acp")],
    Discriminator(_agent_settings_discriminator),
]
"""Discriminated union over the agent-settings variants.

Use :func:`validate_agent_settings` or a :class:`~pydantic.TypeAdapter`
to validate/construct instances from raw payloads. Use
:func:`default_agent_settings` for the default (LLM-agent) shape.

Named ``AgentSettingsConfig`` rather than ``AgentSettings`` because the
old concrete ``AgentSettings`` class was removed after its deprecation
deadline. Use this union for fields that accept any supported settings
variant.
"""


_AGENT_SETTINGS_ADAPTER: TypeAdapter[
    OpenHandsAgentSettings | LLMAgentSettings | ACPAgentSettings
] = TypeAdapter(AgentSettingsConfig)


def validate_agent_settings(
    data: Any,
    *,
    context: Mapping[str, Any] | None = None,
) -> OpenHandsAgentSettings | LLMAgentSettings | ACPAgentSettings:
    """Load and validate an agent-settings payload.

    Persisted payloads are migrated to the current schema version before
    validation, including legacy ``agent_kind: "llm"`` payloads from before the
    ``OpenHandsAgentSettings`` rename.
    """
    if isinstance(data, OpenHandsAgentSettings | ACPAgentSettings):
        return data
    payload = _apply_persisted_migrations(
        data,
        current_version=AGENT_SETTINGS_SCHEMA_VERSION,
        migrations=_AGENT_SETTINGS_MIGRATIONS,
        payload_name="AgentSettings",
    )
    # The v1->v2 migration renames the deprecated ``agent_kind: 'llm'`` tag, but
    # only while advancing ``schema_version``. A payload already at the current
    # version keeps the ``llm`` tag and would dispatch to the deprecated
    # ``LLMAgentSettings`` subclass; canonicalize unconditionally so the loader
    # always returns a ``{openhands, acp}`` variant.
    if payload.get("agent_kind") == "llm":
        payload["agent_kind"] = "openhands"
    return _AGENT_SETTINGS_ADAPTER.validate_python(payload, context=context)


def _merge_patch(base: dict[str, Any], diff: Mapping[str, Any]) -> dict[str, Any]:
    """Apply an RFC 7386 JSON Merge Patch.

    Nested mappings merge recursively; ``None`` deletes a key; every other value
    overwrites. Matches the merge semantics used by the settings stores so call
    sites can delegate without a behavior change.
    """
    result = dict(base)
    for key, value in diff.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge_patch(result[key], value)
        else:
            result[key] = value
    return result


def apply_agent_settings_diff(
    base: Any,
    diff: Mapping[str, Any] | None,
    *,
    context: Mapping[str, Any] | None = None,
) -> OpenHandsAgentSettings | ACPAgentSettings:
    """Apply a sparse agent-settings diff to a base, narrowing on ``agent_kind``.

    ``agent_kind`` is a one-way narrowing gate, never a conversion knob:

    * When ``diff`` changes ``agent_kind``, start from a *fresh* base for the
      target variant. Deep-merging across the union boundary would either fail
      validation (ACP's nullable ``agent_context`` is invalid for OpenHands) or
      silently drop the outgoing variant's fields (the variants ignore unknown
      keys), producing a mongrel row.
    * When ``agent_kind`` is unchanged or omitted, deep-merge the diff within
      the variant (``None`` unsets a key, per :func:`_merge_patch`).

    ``base`` may be a raw persisted mapping or a settings instance; it is
    migrated and validated first. The merged result is re-validated against
    :data:`AgentSettingsConfig`, so the return is always a canonical variant.
    This is the single owner of agent-settings diff application; settings stores
    should delegate here instead of hand-rolling the dump/merge/validate dance.
    """
    base_settings = validate_agent_settings(base, context=context)
    if not diff:
        return base_settings
    new_kind = diff.get("agent_kind")
    if new_kind and new_kind != base_settings.agent_kind:
        merged = _merge_patch({"agent_kind": new_kind}, diff)
    else:
        merged = _merge_patch(
            base_settings.model_dump(mode="json", context={"expose_secrets": True}),
            diff,
        )
    return validate_agent_settings(merged, context=context)


def default_agent_settings() -> OpenHandsAgentSettings:
    """Return a default :class:`OpenHandsAgentSettings` instance.

    This is the drop-in replacement for the old bare ``AgentSettings()``
    constructor call — the default-ever-since variant is the LLM agent.
    """
    return OpenHandsAgentSettings()


def create_agent_from_settings(
    settings: OpenHandsAgentSettings | ACPAgentSettings,
) -> AgentBase:
    """Dispatch to the variant's ``create_agent()`` method.

    Returns either :class:`~openhands.sdk.agent.Agent` (LLM variant) or
    :class:`~openhands.sdk.agent.ACPAgent` (ACP variant).
    """
    return settings.create_agent()


def export_agent_settings_schema() -> SettingsSchema:
    """Export a combined schema for the :data:`AgentSettingsConfig` union.

    Walks both variants, tags each non-shared section with its variant,
    and returns a single :class:`SettingsSchema`. The discriminator
    (``agent_kind``) is intentionally **not** emitted as a schema field
    — each variant lives on its own settings page in the GUI, and the
    page injects the correct ``agent_kind`` value on save. Sections
    carry a ``variant`` tag (``'openhands'``, ``'acp'``, or ``None`` for
    shared) so the frontend can filter by the page's variant.
    """
    llm_schema = OpenHandsAgentSettings.export_schema()
    acp_schema = ACPAgentSettings.export_schema()

    merged_sections: list[SettingsSectionSchema] = []
    merged_by_key: dict[tuple[str, str | None], SettingsSectionSchema] = {}

    def _merge(schema: SettingsSchema, default_variant: str) -> None:
        for section in schema.sections:
            # "general" is shared across variants; tag non-shared keys
            # with the variant so the GUI can filter sections by variant.
            if section.key == _GENERAL_SECTION_KEY and section.variant is None:
                effective_variant: str | None = None
            else:
                effective_variant = section.variant or default_variant

            existing = merged_by_key.get((section.key, effective_variant))
            if existing is None:
                merged = section.model_copy(update={"variant": effective_variant})
                merged_by_key[(section.key, effective_variant)] = merged
                merged_sections.append(merged)
            else:
                # Same (key, variant) across invocations — union fields by key.
                seen_keys = {f.key for f in existing.fields}
                for field in section.fields:
                    if field.key not in seen_keys:
                        existing.fields.append(field)

    _merge(llm_schema, default_variant="openhands")
    _merge(acp_schema, default_variant="acp")

    return SettingsSchema(model_name="AgentSettings", sections=merged_sections)


def settings_section_metadata(field: FieldInfo) -> SettingsSectionMetadata | None:
    extra = field.json_schema_extra
    if not isinstance(extra, dict):
        return None

    metadata = extra.get(SETTINGS_SECTION_METADATA_KEY)
    if metadata is None:
        return None
    return SettingsSectionMetadata.model_validate(metadata)


def settings_metadata(field: FieldInfo) -> SettingsFieldMetadata | None:
    extra = field.json_schema_extra
    if not isinstance(extra, dict):
        return None

    metadata = extra.get(SETTINGS_METADATA_KEY)
    if metadata is None:
        return None
    return SettingsFieldMetadata.model_validate(metadata)


_GENERAL_SECTION_KEY = "general"
_GENERAL_SECTION_LABEL = "General"
_GENERAL_SECTION_METADATA = SettingsSectionMetadata(
    key=_GENERAL_SECTION_KEY,
    label=_GENERAL_SECTION_LABEL,
)


def export_settings_schema(model: type[BaseModel]) -> SettingsSchema:
    """Export a structured settings schema for a Pydantic settings model.

    The returned schema groups nested models into sections and describes each
    exported field with its label, type, default, dependencies, choices, and
    whether the value should be treated as secret input.
    """
    sections: list[SettingsSectionSchema] = []
    sections_by_key: dict[str, SettingsSectionSchema] = {}

    def ensure_section(metadata: SettingsSectionMetadata) -> SettingsSectionSchema:
        section = sections_by_key.get(metadata.key)
        if section is not None:
            return section
        section = SettingsSectionSchema(
            key=metadata.key,
            label=metadata.label or _humanize_name(metadata.key),
            fields=[],
            variant=getattr(metadata, "variant", None),
        )
        sections_by_key[metadata.key] = section
        sections.append(section)
        return section

    for field_name, field in model.model_fields.items():
        explicit_section_metadata = settings_section_metadata(field)
        section_metadata = explicit_section_metadata or _GENERAL_SECTION_METADATA
        nested_models = _nested_model_types(field.annotation)

        # Nested section (e.g., llm, condenser, critic)
        if explicit_section_metadata is not None and nested_models:
            section_default = field.get_default(call_default_factory=True)
            section = ensure_section(explicit_section_metadata)
            seen_nested_fields: dict[str, SettingsFieldSchema] = {}
            for nested_model in nested_models:
                for nested_key, nested_field in nested_model.model_fields.items():
                    if nested_field.exclude:
                        continue
                    existing_field = seen_nested_fields.get(nested_key)
                    if existing_field is not None:
                        existing_choice_values = {
                            choice.value for choice in existing_field.choices
                        }
                        for choice in _extract_choices(nested_field.annotation):
                            if choice.value not in existing_choice_values:
                                existing_field.choices.append(choice)
                                existing_choice_values.add(choice.value)
                        continue
                    metadata = settings_metadata(nested_field)
                    default_value = None
                    if isinstance(section_default, BaseModel) and hasattr(
                        section_default, nested_key
                    ):
                        default_value = getattr(section_default, nested_key)
                    field_schema = SettingsFieldSchema(
                        key=f"{explicit_section_metadata.key}.{nested_key}",
                        label=(
                            metadata.label
                            if metadata is not None and metadata.label is not None
                            else _humanize_name(nested_key)
                        ),
                        description=nested_field.description,
                        section=section.key,
                        section_label=section.label,
                        value_type=_infer_value_type(nested_field.annotation),
                        default=_normalize_default(default_value),
                        prominence=(
                            metadata.prominence
                            if metadata is not None
                            else SettingProminence.MINOR
                        ),
                        depends_on=[
                            f"{explicit_section_metadata.key}.{dependency}"
                            for dependency in (
                                metadata.depends_on if metadata is not None else ()
                            )
                        ],
                        secret=_contains_secret(nested_field.annotation),
                        choices=_extract_choices(nested_field.annotation),
                        # Field-level variant falls back to the enclosing
                        # section's variant — nested fields inherit their
                        # parent section's variant by default.
                        variant=(
                            (metadata.variant if metadata is not None else None)
                            or section.variant
                        ),
                    )
                    seen_nested_fields[nested_key] = field_schema
                    section.fields.append(field_schema)
            continue

        metadata = settings_metadata(field)
        if metadata is None:
            continue

        default_value = field.get_default(call_default_factory=True)
        section = ensure_section(section_metadata)
        section.fields.append(
            SettingsFieldSchema(
                key=field_name,
                label=(
                    metadata.label
                    if metadata.label is not None
                    else _humanize_name(field_name)
                ),
                description=field.description,
                section=section.key,
                section_label=section.label,
                value_type=_infer_value_type(field.annotation),
                default=_normalize_default(default_value),
                prominence=metadata.prominence,
                depends_on=list(metadata.depends_on),
                secret=_contains_secret(field.annotation),
                choices=_extract_choices(field.annotation),
                # Top-level field: use its own variant if set, otherwise
                # fall back to the enclosing section's variant.
                variant=metadata.variant or section.variant,
            )
        )

    return SettingsSchema(model_name=model.__name__, sections=sections)


def _nested_model_type(annotation: Any) -> type[BaseModel] | None:
    candidates = _nested_model_types(annotation)
    if len(candidates) != 1:
        return None

    return candidates[0]


def _nested_model_types(annotation: Any) -> tuple[type[BaseModel], ...]:
    seen: set[type[BaseModel]] = set()
    models: list[type[BaseModel]] = []
    for candidate in _annotation_options(annotation):
        if (
            isinstance(candidate, type)
            and issubclass(candidate, BaseModel)
            and candidate not in seen
        ):
            seen.add(candidate)
            models.append(candidate)
    return tuple(models)


def _annotation_options(annotation: Any) -> tuple[Any, ...]:
    origin = get_origin(annotation)
    if origin is None or origin is Literal:
        return (annotation,)
    if origin in (list, tuple, set, frozenset, dict):
        return (annotation,)

    options: list[Any] = []
    for arg in get_args(annotation):
        if arg is type(None):
            continue
        options.extend(_annotation_options(arg))
    return tuple(options) or (annotation,)


def _contains_secret(annotation: Any) -> bool:
    return any(option is SecretStr for option in _annotation_options(annotation))


def _infer_value_type(annotation: Any) -> SettingsValueType:
    choices = _choice_values(annotation)
    if choices:
        return _value_type_for_values(choices)

    options = _annotation_options(annotation)
    if all(_is_stringish(option) for option in options):
        return "string"
    if all(option is bool for option in options):
        return "boolean"
    if all(option is int for option in options):
        return "integer"
    if all(option in (int, float) for option in options):
        return "number"
    if all(_is_array_annotation(option) for option in options):
        return "array"
    if all(_is_object_annotation(option) for option in options):
        return "object"
    return "string"


def _is_stringish(annotation: Any) -> bool:
    return annotation in (str, SecretStr, Path)


def _is_array_annotation(annotation: Any) -> bool:
    return get_origin(annotation) in (list, tuple, set, frozenset)


def _is_object_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is dict:
        return True
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _choice_values(annotation: Any) -> list[SettingsChoiceValue]:
    inner = _annotation_options(annotation)
    if len(inner) != 1:
        return []

    candidate = inner[0]
    origin = get_origin(candidate)
    if origin is Literal:
        return [
            value
            for value in get_args(candidate)
            if isinstance(value, (bool, int, float, str))
        ]
    if isinstance(candidate, type) and issubclass(candidate, Enum):
        return [
            member.value
            for member in candidate
            if isinstance(member.value, (bool, int, float, str))
        ]
    return []


def _value_type_for_values(values: list[SettingsChoiceValue]) -> SettingsValueType:
    if all(isinstance(value, bool) for value in values):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return "integer"
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in values
    ):
        return "number"
    return "string"


def _extract_choices(annotation: Any) -> list[SettingsChoice]:
    inner = _annotation_options(annotation)
    if len(inner) != 1:
        return []

    candidate = inner[0]
    origin = get_origin(candidate)
    if origin is Literal:
        return [
            SettingsChoice(value=value, label=str(value))
            for value in get_args(candidate)
            if isinstance(value, (bool, int, float, str))
        ]
    if isinstance(candidate, type) and issubclass(candidate, Enum):
        return [
            SettingsChoice(
                value=member.value,
                label=_humanize_name(member.name),
            )
            for member in candidate
            if isinstance(member.value, (bool, int, float, str))
        ]
    return []


def _normalize_default(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return None
    if isinstance(value, Enum):
        return _normalize_default(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _normalize_default(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalize_default(item) for item in value]
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return None


def _humanize_name(name: str) -> str:
    acronyms = {"api", "aws", "id", "llm", "url"}
    words = []
    for part in name.split("_"):
        words.append(part.upper() if part in acronyms else part.capitalize())
    return " ".join(words)
