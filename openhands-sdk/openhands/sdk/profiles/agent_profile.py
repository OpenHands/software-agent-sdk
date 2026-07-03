"""``AgentProfile`` — the named, reference-bearing agent launch spec.

A separate ``Discriminator("agent_kind")`` + ``Tag`` union from
:data:`~openhands.sdk.settings.model.AgentSettingsConfig`: the profile carries
*references* (``llm_profile_ref`` / ``mcp_server_refs``) and is secret-free at
rest, whereas the settings union embeds the resolved ``llm`` / ``mcp_config``.
See epic #3713 for the resolution model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    ValidationError,
)

from openhands.sdk.settings.model import (
    ACPServerKind,
    CondenserSettingsConfig,
    CriticMode,
    LLMSummarizingCondenserSettings,
    VerificationSettings,
)
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool


AGENT_PROFILE_SCHEMA_VERSION = 1


class ProfileVerificationSettings(BaseModel):
    """Secret-free critic/refinement policy for a profile.

    The non-credential subset of
    :class:`~openhands.sdk.settings.model.VerificationSettings`: ``critic_api_key``
    is omitted so the profile holds no secret. The critic reuses the resolved
    LLM profile's key (the existing ``critic_api_key=None`` behavior).
    """

    critic_enabled: bool = False
    critic_mode: CriticMode = "finish_and_message"
    enable_iterative_refinement: bool = False
    critic_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    max_refinement_iterations: int = Field(default=3, ge=1)
    critic_server_url: str | None = None
    critic_model_name: str | None = None


def build_profile_verification(
    v: VerificationSettings,
) -> ProfileVerificationSettings:
    """Project the secret-free subset of ``VerificationSettings`` onto a profile.

    Drops ``critic_api_key`` — the profile is secret-free; the critic reuses the
    resolved LLM profile's key. Hoisted from the agent-server router so the
    local and cloud seeds build verification identically.
    """
    return ProfileVerificationSettings(
        critic_enabled=v.critic_enabled,
        critic_mode=v.critic_mode,
        enable_iterative_refinement=v.enable_iterative_refinement,
        critic_threshold=v.critic_threshold,
        max_refinement_iterations=v.max_refinement_iterations,
        critic_server_url=v.critic_server_url,
        critic_model_name=v.critic_model_name,
    )


class AgentProfileBase(BaseModel):
    """Shared identity + provenance fields for every ``AgentProfile`` variant.

    ``extra="forbid"`` is what gives the union its cross-variant safety: a
    payload tagged ``agent_kind="acp"`` that also carries ``llm_profile_ref``
    (or an OpenHands payload carrying ``acp_*``) is rejected rather than
    silently dropping the foreign field into a mongrel profile.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=AGENT_PROFILE_SCHEMA_VERSION, ge=1)
    id: UUID = Field(
        default_factory=uuid4,
        description=(
            "Stable provenance handle for this profile. Conversations record "
            "this UUID; it never changes, even when the profile is renamed."
        ),
    )
    name: str = Field(
        min_length=1,
        description="Human-facing, renameable key shown in the profile picker.",
    )
    revision: int = Field(
        default=0,
        ge=0,
        description="Monotonic revision counter, bumped on each saved edit.",
    )
    # null = all of the user's MCP servers; [] = none; a non-null list filters
    # to the named keys. null and [] are deliberately distinct.
    mcp_server_refs: list[str] | None = Field(
        default=None,
        description=(
            "Which of the user's globally configured MCP servers to expose. "
            "null = all; [] = none; a non-null list = filter to the named keys."
        ),
    )
    # On the base because both variants consume skills as prompt context. null
    # (all discovered) and [] (none) are distinct; the default is [] — NOT null,
    # unlike ``mcp_server_refs`` — because auto-injecting the whole catalog isn't
    # behavior-preserving and a profile persisted before this field existed must
    # not silently start pulling it on load. ``default_factory=list`` makes an
    # absent field ([]) distinct from an explicit ``null`` (None), the opt-in for
    # "all discovered".
    skill_refs: list[str] | None = Field(
        default_factory=list,
        description=(
            "Which of the server-discovered skills to expose in the agent's "
            "prompt, selected by name (mirrors ``mcp_server_refs`` over "
            "``mcp_config``). null = all discovered; [] = none (the default); a "
            "non-null list = filter to the named skills. For OpenHands profiles, "
            "any explicitly embedded ``skills`` are always included on top of "
            "the filtered set."
        ),
    )


class OpenHandsAgentProfile(AgentProfileBase):
    """``agent_kind="openhands"`` profile — references an LLM profile by name.

    Mirrors the configurable surface of
    :class:`~openhands.sdk.settings.model.OpenHandsAgentSettings`, except the
    concrete ``llm`` is replaced by :attr:`llm_profile_ref` (resolved against
    the LLM profile store) and ``mcp_config`` by the inherited
    :attr:`~AgentProfileBase.mcp_server_refs`.
    """

    agent_kind: Literal["openhands"] = Field(
        default="openhands",
        description=(
            "Discriminator for the ``AgentProfile`` union. ``'openhands'`` "
            "selects the standard built-in OpenHands agent."
        ),
    )
    llm_profile_ref: str = Field(
        min_length=1,
        description=(
            "Name of the saved LLM profile to resolve for this agent. The "
            "profile itself stores no LLM credential — it lives on the "
            "referenced LLM profile."
        ),
    )
    agent: str = Field(
        default="CodeActAgent",
        description="Agent class to build.",
    )
    # Same tri-state as the resolved settings' ``tools``: passed through
    # verbatim by the resolver, so ``create_agent`` is the single defaulting
    # point (#3978). Secret-free by construction (``Tool`` is name + params).
    tools: list[Tool] | None = Field(
        default=None,
        description=(
            "Tool selection for the resolved agent. None (the default) = the "
            "server's standard tool set; [] = an explicitly bare agent; a "
            "non-empty list is used exactly as given."
        ),
    )
    skills: list[Skill] = Field(
        default_factory=list,
        description="Skills that extend the agent's context.",
    )
    system_message_suffix: str | None = Field(
        default=None,
        description="Optional suffix appended to the system prompt.",
    )
    condenser: CondenserSettingsConfig = Field(
        default_factory=LLMSummarizingCondenserSettings,
        description="Condenser settings for the agent.",
    )
    verification: ProfileVerificationSettings = Field(
        default_factory=ProfileVerificationSettings,
        description="Critic/verification policy (secret-free; no critic_api_key).",
    )
    enable_sub_agents: bool = Field(
        default=False,
        description="Enable sub-agent delegation via TaskToolSet.",
    )
    enable_switch_llm_tool: bool = Field(
        default=True,
        description=(
            "Enable the built-in switch_llm tool for switching between saved "
            "LLM profiles. Defaults True to match the global agent settings "
            "default (AgentSettingsConfig.enable_switch_llm_tool)."
        ),
    )
    tool_concurrency_limit: int = Field(
        default=1,
        ge=1,
        description=(
            "Maximum number of tool calls to execute concurrently per agent "
            "step. 1 = sequential (default)."
        ),
    )


class ACPAgentProfile(AgentProfileBase):
    """``agent_kind="acp"`` profile — names an ACP backend, stores no credential.

    There is no ``llm_profile_ref`` and no embedded credential: the ACP
    subprocess makes its own model calls, and the provider's credential
    (env-var name) is derived from :attr:`acp_server` via the
    :data:`~openhands.sdk.settings.acp_providers.ACP_PROVIDERS` registry. The
    value rides the conversation secrets channel, never the profile.
    """

    agent_kind: Literal["acp"] = Field(
        default="acp",
        description=(
            "Discriminator for the ``AgentProfile`` union. ``'acp'`` selects an "
            "ACP-delegating agent."
        ),
    )
    # ``skill_refs`` is inherited from ``AgentProfileBase`` with the safe []
    # default (no discovered skills injected unless a null/list is set) — ACP
    # agents own their tooling, so that default is exactly right here.
    acp_server: ACPServerKind = Field(
        default="claude-code",
        description=(
            "Which ACP-compatible backend to launch. The provider's credential "
            "env-var name is derived from this via the ACP_PROVIDERS registry."
        ),
    )
    acp_model: str | None = Field(
        default=None,
        description=(
            "Model identifier for the ACP server (e.g. 'claude-opus-4-8'). "
            "Leave blank to let the server pick its default."
        ),
    )
    acp_session_mode: str | None = Field(
        default=None,
        description=(
            "Session mode ID (e.g. 'bypassPermissions'). Leave blank to "
            "auto-detect from the ACP server type."
        ),
    )
    acp_prompt_timeout: float = Field(
        default=1800.0,
        gt=0,
        description=(
            "Inactivity timeout (seconds) for a single ACP prompt() round-trip; "
            "resets on every update from the server."
        ),
    )
    acp_command: str | None = Field(
        default=None,
        description=(
            "Optional explicit command to launch the ACP subprocess. Leave "
            "blank to use the default for ``acp_server``."
        ),
    )
    acp_args: list[str] | None = Field(
        default=None,
        description="Additional arguments appended to the ACP server command.",
    )


class LaunchedAgentProfile(BaseModel):
    """Provenance snapshot recorded when an agent profile launches a conversation.

    Stored on ``StoredConversation`` and projected onto ``ConversationInfo`` so
    ts-client ``deriveSwitchPlan`` can identify which agent profile is current
    without fragile settings-comparison. See #3720.
    """

    agent_profile_id: UUID = Field(
        description="Stable id of the agent profile that launched the conversation.",
    )
    revision: int = Field(
        ge=0,
        description="Revision of the agent profile at launch time.",
    )


def _agent_profile_discriminator(value: Any) -> str:
    """Discriminator for :data:`AgentProfile` — defaults to ``'openhands'``.

    A payload without an explicit ``agent_kind`` is treated as the standard
    OpenHands variant, mirroring
    :func:`~openhands.sdk.settings.model._agent_settings_discriminator`.
    """
    if isinstance(value, BaseModel):
        return getattr(value, "agent_kind", "openhands")
    if isinstance(value, Mapping):
        return value.get("agent_kind", "openhands")
    return "openhands"


AgentProfile = Annotated[
    Annotated[OpenHandsAgentProfile, Tag("openhands")]
    | Annotated[ACPAgentProfile, Tag("acp")],
    Discriminator(_agent_profile_discriminator),
]
"""Discriminated union over the agent-profile variants.

Use :func:`validate_agent_profile` (or a :class:`~pydantic.TypeAdapter`) to
validate/construct instances from raw payloads.
"""


PersistedProfileMigrator = Callable[[dict[str, Any]], dict[str, Any]]

# Registered per *source* schema_version; each migrator returns a payload with
# an advanced ``schema_version``. Empty while only v1 exists — the first schema
# bump adds ``{1: _migrate_v1_to_v2}`` here.
_AGENT_PROFILE_MIGRATIONS: dict[int, PersistedProfileMigrator] = {}


def _apply_persisted_migrations(payload: dict[str, Any]) -> dict[str, Any]:
    """Bring a persisted ``AgentProfile`` payload up to the current schema.

    A payload missing ``schema_version`` predates versioning (or is a freshly
    authored dict); canonicalize it by stamping the field to ``1``. Otherwise
    the version is validated and walked forward through
    :data:`_AGENT_PROFILE_MIGRATIONS`. Mirrors the migration dispatcher in
    ``settings/model.py``.
    """
    migrated = dict(payload)
    version_raw = migrated.get("schema_version")
    if version_raw is None:
        migrated["schema_version"] = 1
        version = 1
    elif isinstance(version_raw, int) and not isinstance(version_raw, bool):
        version = version_raw
    else:
        raise TypeError(
            "AgentProfile schema_version must be an integer, got "
            f"{type(version_raw).__name__}."
        )

    if version < 0:
        raise ValueError("AgentProfile schema_version must be non-negative.")
    if version > AGENT_PROFILE_SCHEMA_VERSION:
        raise ValueError(
            f"AgentProfile schema_version {version} is newer than supported "
            f"version {AGENT_PROFILE_SCHEMA_VERSION}."
        )

    while version < AGENT_PROFILE_SCHEMA_VERSION:
        migrate = _AGENT_PROFILE_MIGRATIONS.get(version)
        if migrate is None:
            raise ValueError(
                f"No migration registered for AgentProfile schema_version {version}."
            )
        migrated = migrate(dict(migrated))
        next_version = migrated.get("schema_version")
        if not isinstance(next_version, int) or isinstance(next_version, bool):
            raise ValueError(
                f"Migration for AgentProfile schema_version {version} did not "
                "produce a valid integer schema_version."
            )
        if next_version <= version:
            raise ValueError(
                f"Migration for AgentProfile schema_version {version} did not "
                "advance the schema_version."
            )
        version = next_version

    return migrated


_AGENT_PROFILE_ADAPTER: TypeAdapter[OpenHandsAgentProfile | ACPAgentProfile] = (
    TypeAdapter(AgentProfile)
)


def validate_agent_profile(
    data: Any,
    *,
    context: Mapping[str, Any] | None = None,
) -> OpenHandsAgentProfile | ACPAgentProfile:
    """Load and validate an ``AgentProfile`` payload, narrowing on ``agent_kind``.

    Already-validated instances pass through unchanged. Raw mappings are
    migrated to the current schema version, then validated against
    :data:`AgentProfile`, so the return is always a canonical variant.
    """
    if isinstance(data, OpenHandsAgentProfile | ACPAgentProfile):
        return data
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
    elif isinstance(data, Mapping):
        payload = dict(data)
    else:
        raise TypeError("AgentProfile payload must be a mapping or BaseModel.")
    payload = _apply_persisted_migrations(payload)
    return _AGENT_PROFILE_ADAPTER.validate_python(payload, context=context)


def safe_validation_error_detail(exc: ValidationError) -> list[dict[str, Any]]:
    """Secret-safe ``detail`` for a 422 from a failed profile validation.

    Surfaces only ``loc``/``type`` per error and **drops ``msg``/``input``** — a
    nested ``skills[].mcp_tools`` ``MCPConfig`` error embeds the offending input,
    which may carry secrets. Shaped like FastAPI's request-validation ``detail``
    (a list of error objects) so routers can hand it straight to ``HTTPException``.
    Hoisted from the agent-server router so the local and cloud routers redact
    identically.
    """
    return [{"loc": err["loc"], "type": err["type"]} for err in exc.errors()]
