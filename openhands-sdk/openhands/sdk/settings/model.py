from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeVar, get_args, get_origin
from uuid import UUID

from fastmcp.mcp_config import MCPConfig
from pydantic import (
    BaseModel,
    Discriminator,
    Field,
    SecretStr,
    Tag,
    TypeAdapter,
    field_serializer,
    field_validator,
)
from pydantic.fields import FieldInfo

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.request import SendMessageRequest
from openhands.sdk.hooks import HookConfig
from openhands.sdk.llm import LLM
from openhands.sdk.plugin import PluginSource
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.tool import Tool
from openhands.sdk.workspace import LocalWorkspace

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
    from openhands.sdk.context.condenser import LLMSummarizingCondenser
    from openhands.sdk.critic.base import CriticBase


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
    enabled: bool = Field(
        default=True,
        description="Enable the LLM summarizing condenser.",
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
        description="Maximum number of events kept before the condenser runs.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Max size",
                prominence=SettingProminence.MINOR,
                depends_on=("enabled",),
            ).model_dump()
        },
    )


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

    # -- Deprecated (moved to ConversationSettings) --
    confirmation_mode: bool = Field(
        default=False,
        description="Require user confirmation before executing risky actions.",
        deprecated=(
            "Deprecated in 1.17.0; use ConversationSettings.confirmation_mode "
            "instead. Will be removed in 1.22.0."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Confirmation mode",
                prominence=SettingProminence.MAJOR,
            ).model_dump()
        },
    )
    security_analyzer: SecurityAnalyzerType | None = Field(
        default=None,
        description=("Security analyzer that evaluates actions before execution."),
        deprecated=(
            "Deprecated in 1.17.0; use ConversationSettings.security_analyzer "
            "instead. Will be removed in 1.22.0."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Security analyzer",
                prominence=SettingProminence.MAJOR,
                depends_on=("confirmation_mode",),
            ).model_dump()
        },
    )

    @field_validator("confirmation_mode", mode="before")
    @classmethod
    def _warn_confirmation_mode(cls, v: Any) -> Any:
        if v:
            from openhands.sdk.utils.deprecation import warn_deprecated

            warn_deprecated(
                "VerificationSettings.confirmation_mode",
                deprecated_in="1.17.0",
                removed_in="1.22.0",
                details="Use ConversationSettings.confirmation_mode instead.",
            )
        return v

    @field_validator("security_analyzer", mode="before")
    @classmethod
    def _warn_security_analyzer(cls, v: Any) -> Any:
        if v is not None:
            from openhands.sdk.utils.deprecation import warn_deprecated

            warn_deprecated(
                "VerificationSettings.security_analyzer",
                deprecated_in="1.17.0",
                removed_in="1.22.0",
                details="Use ConversationSettings.security_analyzer instead.",
            )
        return v


def _default_llm_settings() -> LLM:
    model = LLM.model_fields["model"].get_default()
    assert isinstance(model, str)
    return LLM(model=model)


_RequestT = TypeVar("_RequestT")

AGENT_SETTINGS_SCHEMA_VERSION = 1
CONVERSATION_SETTINGS_SCHEMA_VERSION = 1

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


def _migrate_conversation_settings_v0_to_v1(
    payload: dict[str, Any],
) -> dict[str, Any]:
    migrated = dict(payload)
    migrated["schema_version"] = 1
    return migrated


_AGENT_SETTINGS_MIGRATIONS: dict[int, PersistedSettingsMigrator] = {
    0: _migrate_agent_settings_v0_to_v1,
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
        description="Agent definitions for DelegateTool / TaskSetTool.",
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


AgentKind = Literal["openhands", "acp"]

ACPServerKind = Literal["claude-code", "codex", "gemini-cli", "custom"]
"""Known ACP backend servers the GUI can pick from.

``custom`` means the user supplies the raw ``acp_command`` themselves;
the other choices map to a default npx command (see
:data:`_DEFAULT_ACP_COMMANDS`).
"""


_DEFAULT_ACP_COMMANDS: dict[str, list[str]] = {
    "claude-code": ["npx", "-y", "@agentclientprotocol/claude-agent-acp"],
    "codex": ["npx", "-y", "@zed-industries/codex-acp"],
    # gemini-cli's ACP mode is activated via ``--acp`` (``--experimental-acp``
    # is deprecated in the gemini-cli >=0.38 releases).
    "gemini-cli": ["npx", "-y", "@google/gemini-cli", "--acp"],
}


class OpenHandsAgentSettings(BaseModel):
    """Settings for a standard LLM-backed :class:`Agent`.

    This is the long-standing ``AgentSettings`` shape; fields here build
    the default ``Agent`` (LLM + tools + MCP + condenser + critic).
    """

    schema_version: int = Field(default=AGENT_SETTINGS_SCHEMA_VERSION, ge=1)
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
    condenser: CondenserSettings = Field(
        default_factory=CondenserSettings,
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

    @field_validator("mcp_config", mode="before")
    @classmethod
    def _normalize_empty_mcp_config(cls, value: Any) -> Any:
        if value in (None, {}):
            return None
        return value

    @field_serializer("mcp_config")
    def _serialize_mcp_config(self, value: MCPConfig | None) -> dict[str, Any]:
        if value is None:
            return {}
        return value.model_dump(exclude_none=True, exclude_defaults=True)

    @classmethod
    def export_schema(cls) -> SettingsSchema:
        """Export a structured schema describing configurable agent settings."""
        return export_settings_schema(cls)

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

        return Agent(
            llm=self.llm,
            tools=self.tools,
            mcp_config=self._serialize_mcp_config(self.mcp_config),
            agent_context=self.agent_context,
            condenser=self.build_condenser(self.llm),
            critic=self.build_critic(),
        )

    def build_condenser(self, llm: LLM) -> LLMSummarizingCondenser | None:
        """Create a condenser from these settings, or ``None`` if disabled."""
        if not self.condenser.enabled:
            return None

        from openhands.sdk.context.condenser import LLMSummarizingCondenser

        return LLMSummarizingCondenser(llm=llm, max_size=self.condenser.max_size)

    def build_critic(self) -> CriticBase | None:
        """Create an :class:`APIBasedCritic` from these settings.

        Returns ``None`` when the critic is disabled or when the LLM
        has no ``api_key`` (the critic service requires authentication).

        If ``verification.critic_server_url`` or
        ``verification.critic_model_name`` are set they override the
        ``APIBasedCritic`` defaults, allowing deployments to route
        through a custom endpoint (e.g. an LLM proxy).
        """
        if not self.verification.critic_enabled:
            return None

        api_key = self.llm.api_key
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


class ACPAgentSettings(BaseModel):
    """Settings for an ACP (Agent Client Protocol) agent.

    ``create_agent()`` returns an :class:`ACPAgent` that delegates to a
    subprocess ACP server.  The ACP server manages its own system prompt,
    tools, MCP, and (primary) LLM calls; those fields from
    :class:`OpenHandsAgentSettings` do not apply here.

    The :attr:`llm` field is kept (optional) so that cost/token metrics
    can be attributed to a real model — ``ACPAgent`` uses this purely for
    bookkeeping and pricing lookups, not for making LLM requests.
    """

    schema_version: int = Field(default=AGENT_SETTINGS_SCHEMA_VERSION, ge=1)
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
    acp_env: dict[str, str] = Field(
        default_factory=dict,
        description="Extra environment variables passed to the ACP subprocess.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="ACP environment variables",
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
            "Model identifier for the ACP server to use (e.g. "
            "``'claude-opus-4-6'``). claude-agent-acp receives it via session "
            "_meta; codex-acp and gemini-cli via ``set_session_model``. "
            "Leave blank to let the server pick its default."
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
        description="Timeout (seconds) for a single ACP prompt() round-trip.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="ACP prompt timeout (seconds)",
                prominence=SettingProminence.MINOR,
            ).model_dump(),
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="acp",
                label="ACP (Agent Client Protocol)",
                variant="acp",
            ).model_dump(),
        },
    )
    llm: LLM = Field(
        default_factory=_default_llm_settings,
        description=(
            "LLM identity used for cost/token attribution. The ACP subprocess "
            "makes its own model calls; this field is kept so metrics and "
            "pricing lookups can point at a real model id."
        ),
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="llm",
                label="LLM (for metrics)",
                variant="acp",
            ).model_dump()
        },
    )

    @classmethod
    def export_schema(cls) -> SettingsSchema:
        """Export a structured schema describing configurable ACP settings."""
        return export_settings_schema(cls)

    def resolve_acp_command(self) -> list[str]:
        """Return the effective subprocess command for this settings block.

        Uses :attr:`acp_command` verbatim when non-empty; otherwise looks
        up the default for :attr:`acp_server`. Raises ``ValueError`` when
        the server is ``'custom'`` but no explicit command is set (there
        is no sensible default to fall back to).
        """
        if self.acp_command:
            return list(self.acp_command)
        if self.acp_server == "custom":
            raise ValueError(
                "ACPAgentSettings.acp_command must be set when "
                "acp_server='custom' — there is no default to fall back to"
            )
        default = _DEFAULT_ACP_COMMANDS.get(self.acp_server)
        if default is None:
            raise ValueError(
                f"No default ACP command for acp_server={self.acp_server!r}"
            )
        return list(default)

    def create_agent(self) -> ACPAgent:
        """Build an :class:`ACPAgent` from these settings.

        The subprocess command is resolved via :meth:`resolve_acp_command`
        which maps :attr:`acp_server` to a default when no explicit
        :attr:`acp_command` is set.
        """
        from openhands.sdk.agent import ACPAgent

        return ACPAgent(
            llm=self.llm,
            acp_command=self.resolve_acp_command(),
            acp_args=list(self.acp_args),
            acp_env=dict(self.acp_env),
            acp_model=self.acp_model,
            acp_session_mode=self.acp_session_mode,
            acp_prompt_timeout=self.acp_prompt_timeout,
        )


class LLMAgentSettings(OpenHandsAgentSettings):
    """Deprecated name for :class:`OpenHandsAgentSettings`.

    ``LLMAgentSettings`` was the public class name before the v1.19.0 rename.
    It is kept as a :class:`OpenHandsAgentSettings` subclass so existing
    callers keep working, though direct construction now emits a
    :class:`DeprecationWarning`.

    Use :class:`OpenHandsAgentSettings` for all new code.

    Scheduled for removal in v1.22.0.
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

    def model_post_init(self, __context: Any) -> None:
        # Only warn when constructing LLMAgentSettings directly, not subclasses
        # (AgentSettings inherits from LLMAgentSettings and emits its own warning).
        if type(self) is LLMAgentSettings:
            from openhands.sdk.utils.deprecation import warn_deprecated

            warn_deprecated(
                "LLMAgentSettings",
                deprecated_in="1.19.0",
                removed_in="1.22.0",
                details=(
                    "Use ``OpenHandsAgentSettings`` directly. "
                    "``LLMAgentSettings`` was renamed in v1.19.0."
                ),
            )
        super().model_post_init(__context)


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
latter is retained as a (deprecated) concrete class for backwards
compatibility with v1.17.x callers — see :class:`AgentSettings`.
"""


_AGENT_SETTINGS_ADAPTER: TypeAdapter[
    OpenHandsAgentSettings | LLMAgentSettings | ACPAgentSettings
] = TypeAdapter(AgentSettingsConfig)


def validate_agent_settings(
    data: Any,
) -> OpenHandsAgentSettings | LLMAgentSettings | ACPAgentSettings:
    """Validate ``data`` as an :data:`AgentSettingsConfig` discriminated union.

    This is the drop-in replacement for the old
    ``AgentSettings.model_validate(...)`` classmethod.
    """
    return _AGENT_SETTINGS_ADAPTER.validate_python(data)


class AgentSettings(LLMAgentSettings):
    """Deprecated legacy name for :class:`OpenHandsAgentSettings`.

    Before the discriminated-union redesign, ``AgentSettings`` was the
    single concrete class for agent configuration. It is kept as a
    :class:`LLMAgentSettings` subclass (which itself is a
    :class:`OpenHandsAgentSettings` subclass) so every v1.17 attribute and
    method (``agent``, ``llm``, ``tools``, ``mcp_config``,
    ``condenser``, ``verification``, ``build_condenser``,
    ``build_critic``, ``create_agent``, …) resolves through
    inheritance — existing callers keep working, though direct
    construction now emits a :class:`DeprecationWarning`.

    Inherits from :class:`LLMAgentSettings` so that ``agent_kind`` remains
    ``"llm"`` (matching the PyPI 1.19.x API surface seen by the breakage
    checker), while new code should use :class:`OpenHandsAgentSettings`
    directly.

    For new code:

    * Use :class:`OpenHandsAgentSettings` to build an explicit LLM-backed
      agent, or :class:`ACPAgentSettings` for an ACP-delegating one.
    * Use :data:`AgentSettingsConfig` as the type for fields that may
      hold either variant (FastAPI / Pydantic pick the variant from
      the ``agent_kind`` discriminator).
    * Use :func:`validate_agent_settings` to validate raw payloads
      into the correct variant.

    Scheduled for removal in v1.22.0 (5 minor releases after the
    discriminated-union landing in v1.17.1).
    """

    @classmethod
    def from_persisted(
        cls, data: Any
    ) -> OpenHandsAgentSettings | LLMAgentSettings | ACPAgentSettings:
        """Load persisted agent settings, applying any schema migrations."""
        payload = _apply_persisted_migrations(
            data,
            current_version=AGENT_SETTINGS_SCHEMA_VERSION,
            migrations=_AGENT_SETTINGS_MIGRATIONS,
            payload_name="AgentSettings",
        )
        return validate_agent_settings(payload)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # pydantic v2 generates __init__; this stub exists so the API-breakage
        # checker does not flag AgentSettings.__init__ as removed. The
        # deprecation warning is emitted via model_post_init instead.
        super().__init__(*args, **kwargs)

    def model_post_init(self, __context: Any) -> None:
        from openhands.sdk.utils.deprecation import warn_deprecated

        warn_deprecated(
            "AgentSettings",
            deprecated_in="1.17.0",
            removed_in="1.22.0",
            details=(
                "Use ``OpenHandsAgentSettings`` (for an LLM agent) or "
                "``ACPAgentSettings`` (for an ACP agent) directly; use "
                "``AgentSettingsConfig`` as the type for fields that accept "
                "either variant."
            ),
        )
        super().model_post_init(__context)


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
    """Export a combined schema for the :data:`AgentSettings` union.

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
        nested_model = _nested_model_type(field.annotation)

        # Nested section (e.g., llm, condenser, critic)
        if explicit_section_metadata is not None and nested_model is not None:
            section_default = field.get_default(call_default_factory=True)
            section = ensure_section(explicit_section_metadata)
            for nested_key, nested_field in nested_model.model_fields.items():
                if nested_field.exclude:
                    continue
                metadata = settings_metadata(nested_field)
                default_value = None
                if isinstance(section_default, BaseModel):
                    default_value = getattr(section_default, nested_key)
                section.fields.append(
                    SettingsFieldSchema(
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
                )
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
    candidates = _annotation_options(annotation)
    if len(candidates) != 1:
        return None

    candidate = candidates[0]
    if isinstance(candidate, type) and issubclass(candidate, BaseModel):
        return candidate
    return None


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
