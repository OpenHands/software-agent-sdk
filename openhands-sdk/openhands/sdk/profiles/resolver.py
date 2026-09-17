"""Build a conversation's agent from an Agent Profile.

:func:`prepare_agent_launch` is the single place a launch agent is built. A
profile carries *references* (``llm_profile_ref`` / ``mcp_server_refs``) plus a
``disabled_skills`` deny-list and is secret-free at rest; the launch resolves
those against an :class:`AgentLaunchCatalog`, then applies the
runtime-dependent and per-launch pieces (:class:`AgentLaunchRuntime`,
:class:`AgentLaunchAdditions`). :func:`resolve_agent_profile_dry_run` calls the
same function with side effects off, so a preview can never disagree with a
launch. See epic #3713 and #5141.

Skills are *not* modeled like MCP servers. ``mcp_server_refs`` is a safe
allow-list because ``mcp_config`` is a complete, persisted, user-authored map.
The skill catalog is discovered from many incomplete, drifting sources
(user/public/org/project/marketplace), so an allow-list of names would dangle
whenever the authoring catalog differs from the launch catalog. Instead the
caller passes the discovered catalog (``load_all_skills``) and the resolver keeps
all of it except the names in ``disabled_skills`` — a deny-list that can never
dangle (#4017).

Resource-specific secret channels:

- **LLM key** → loaded from the LLM profile store into the resolved ``llm``.
- **MCP env/headers** → ride the filtered ``mcp_config`` (decrypted by the caller).
- **ACP provider creds** → never touched here; they ride
  ``state.secret_registry`` ← ``request.secrets`` wired at conversation-start
  (#3720). The resolver only *enumerates* the required provider secret names
  (via the dry-run) so the editor / ``/materialize`` (#3719) can show set/missing.
"""

from __future__ import annotations

import shlex
from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles.agent_profile import (
    ACPAgentProfile,
    LaunchedAgentProfile,
    OpenHandsAgentProfile,
)
from openhands.sdk.profiles.seed import build_seed_profile
from openhands.sdk.settings.acp_providers import get_acp_provider
from openhands.sdk.settings.model import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    AgentSettingsConfig,
    OpenHandsAgentSettings,
    validate_agent_settings,
)
from openhands.sdk.skills import Skill
from openhands.sdk.tool.defaults import default_tool_specs
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE


if TYPE_CHECKING:
    from openhands.sdk.agent.base import AgentBase
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.llm.llm_profile_store import LLMProfileLoader
    from openhands.sdk.utils.cipher import Cipher


ACPSkillSourcing = Literal["native", "openhands_managed"]
ProfileOrigin = Literal["stored", "inline"]


class ProfileNotFound(Exception):
    """A referenced profile (e.g. ``llm_profile_ref``) does not exist.

    The router (#3719) maps this to HTTP 404.
    """


class DanglingMcpServerRef(Exception):
    """An ``mcp_server_refs`` entry names a server absent from ``mcp_config``.

    The router (#3719) maps this to HTTP 422. :attr:`missing` carries the
    offending key(s).
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        joined = ", ".join(repr(m) for m in missing)
        super().__init__(
            f"MCP server ref(s) not present in the user's MCP config: {joined}"
        )


class AgentLaunchError(ValueError):
    """A launch request that cannot be satisfied as given."""

    code = "invalid_agent_launch"

    def to_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self)}


class UnresolvedProfileReferences(AgentLaunchError):
    """An Agent Profile references an LLM profile or MCP servers that don't exist."""

    code = "unresolved_profile_references"

    def __init__(
        self,
        *,
        llm_profile_ref: str | None = None,
        mcp_server_refs: Sequence[str] = (),
    ) -> None:
        self.llm_profile_ref = llm_profile_ref
        self.mcp_server_refs = list(mcp_server_refs)
        problems = []
        if llm_profile_ref is not None:
            problems.append(f"LLM profile {llm_profile_ref!r} not found")
        if self.mcp_server_refs:
            problems.append(
                "MCP server(s) not configured: " + ", ".join(self.mcp_server_refs)
            )
        super().__init__("; ".join(problems))

    def to_detail(self) -> dict[str, Any]:
        return {
            **super().to_detail(),
            "dangling_llm_profile_ref": self.llm_profile_ref,
            "dangling_mcp_server_refs": self.mcp_server_refs,
        }


class AgentLaunchAdditions(BaseModel):
    """Per-launch additions applied on top of the resolved agent.

    Additions never widen what a profile exposes: they carry no tools, MCP
    servers, skills or secrets.
    """

    model_config = ConfigDict(extra="forbid")

    system_message_suffix_append: str | None = Field(
        default=None,
        max_length=32768,
        description=(
            "Deployment-controlled text appended to the resolved agent's "
            "system-message suffix."
        ),
    )
    llm_profile_ref: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "LLM profile to launch an OpenHands Agent Profile with instead of "
            "its own `llm_profile_ref`. Recorded in `launched_agent_profile`; "
            "the stored profile is not modified. Only valid with "
            "`agent_profile_id` or `agent_profile`."
        ),
    )


class AgentLaunchRuntime(BaseModel):
    """Launch inputs that depend on the runtime the agent will run in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    browser_available: bool = Field(
        default=False,
        description=(
            "Whether the runtime can run the browser tool set. Added to a "
            "profile that uses the default tool set."
        ),
    )
    acp_skill_sourcing: ACPSkillSourcing = Field(
        default="native",
        description=(
            "'native': an ACP CLI reads its own skills, so none are injected. "
            "'openhands_managed': inject the skill catalog (container runtimes)."
        ),
    )
    stream: bool = Field(
        default=False,
        description=(
            "Force token streaming on a profile's LLM. Set by servers that wire "
            "a token callback."
        ),
    )
    load_memory: bool = Field(
        default=False,
        description="The user's global persistent-memory preference.",
    )

    def uses_skill_catalog(self, agent_kind: str) -> bool:
        """Whether a profile of ``agent_kind`` launches with the skill catalog."""
        return agent_kind != "acp" or self.acp_skill_sourcing == "openhands_managed"


@dataclass(frozen=True, kw_only=True)
class AgentLaunchCatalog:
    """Shared resources an Agent Profile's references resolve against.

    ``skills`` is the discovered skill catalog; ``None`` means discovery was not
    run. ``base_settings`` supplies the fields a profile does not model (e.g.
    ``critic_api_key``); only the deprecated ``agent_settings`` launch sets it.
    """

    llm_store: LLMProfileLoader
    mcp_config: Mapping[str, MCPServer]
    skills: Sequence[Skill] | None
    cipher: Cipher | None = None
    base_settings: AgentSettingsConfig | None = None


@dataclass(frozen=True, kw_only=True)
class AgentLaunchPlan:
    """What :func:`prepare_agent_launch` resolved.

    ``settings`` is ``None`` for a raw ``agent`` launch; ``agent`` is ``None``
    when the launch was prepared with ``build_agent=False``.
    ``allowed_secrets`` is the profile's secret allow-list (``None`` =
    unrestricted).
    """

    settings: AgentSettingsConfig | None
    agent: AgentBase | None
    launched_profile: LaunchedAgentProfile | None
    allowed_secrets: frozenset[str] | None


class AgentProfileDiagnostics(BaseModel):
    """Side-effect-free report of what :func:`prepare_agent_launch` would do.

    Consumed by ``POST /{id}/materialize`` (#3719) and the canvas editor. The
    verdict (:attr:`valid`) and :attr:`resolved_settings` come from the same
    launch function a conversation start uses; :attr:`resolved_settings` is the
    redacted settings dump (present only when :attr:`valid`).
    """

    agent_kind: str
    valid: bool = False
    errors: list[str] = Field(default_factory=list)

    # OpenHands LLM reference (the per-launch override when one was given).
    llm_profile_ref: str | None = None
    llm_profile_resolved: bool = False
    llm_api_key_set: bool = False

    # MCP composition (both variants).
    mcp_server_refs: list[str] | None = None
    resolved_mcp_config_keys: list[str] = Field(default_factory=list)
    dangling_mcp_server_refs: list[str] = Field(default_factory=list)

    # Skill selection. ``disabled_skills`` is a deny-list over the discovered
    # catalog, so — unlike ``mcp_server_refs`` — it can never dangle.
    # ``resolved_skills`` is what would actually reach the agent.
    disabled_skills: list[str] = Field(default_factory=list)
    resolved_skills: list[str] = Field(default_factory=list)

    # Secret scope (both variants). ``None`` = every secret the conversation is
    # started with; a list = only those names, with nothing added back.
    secret_refs: list[str] | None = None

    # ACP provider credential channels the editor/materialize checks (ACP only).
    # These are NOT jointly required: authentication needs the API key *or* one
    # of the file-content credentials, and the base URL is optional proxy
    # routing.
    acp_api_key_secret_name: str | None = None
    acp_base_url_secret_name: str | None = None
    acp_file_secret_names: list[str] = Field(default_factory=list)

    # Redacted resolved settings, present iff ``valid``.
    resolved_settings: dict[str, Any] | None = None


def _partition_refs(
    refs: list[str], available: Container[str]
) -> tuple[list[str], list[str]]:
    """Split ``refs`` into order-preserving, de-duplicated ``(resolved, dangling)``."""
    seen: set[str] = set()
    resolved: list[str] = []
    dangling: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        (resolved if ref in available else dangling).append(ref)
    return resolved, dangling


def _compute_mcp_filter(
    mcp_config: Mapping[str, MCPServer],
    refs: list[str] | None,
) -> tuple[dict[str, MCPServer], list[str], list[str]]:
    """Resolve ``mcp_server_refs``: ``None`` keeps every server, a list filters."""
    if refs is None:
        return dict(mcp_config), list(mcp_config), []
    resolved, dangling = _partition_refs(refs, mcp_config)
    return {k: mcp_config[k] for k in resolved}, resolved, dangling


def _apply_disabled_skills(
    available_skills: Sequence[Skill] | None,
    disabled: list[str],
) -> list[Skill]:
    """Filter the discovered skill catalog by a deny-list.

    De-duplicated by name (last occurrence wins, matching ``load_all_skills``)
    so a colliding catalog cannot trip ``AgentContext``'s duplicate-name check.
    """
    if not available_skills:
        return []
    by_name = {s.name: s for s in available_skills}
    denied = set(disabled)
    return [s for s in by_name.values() if s.name not in denied]


def _launch_skills(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    catalog: AgentLaunchCatalog,
    runtime: AgentLaunchRuntime,
) -> list[Skill]:
    if isinstance(profile, OpenHandsAgentProfile):
        return _apply_disabled_skills(catalog.skills, profile.disabled_skills)
    if not runtime.uses_skill_catalog(profile.agent_kind):
        return []
    return _apply_disabled_skills(catalog.skills, [])


def _api_key_set(llm: LLM) -> bool:
    """``True`` when the resolved LLM carries a non-empty, non-redacted key."""
    api_key = llm.api_key
    if api_key is None:
        return False
    value = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
    return bool(value.strip()) and value != REDACTED_SECRET_VALUE


def _acp_credential_channels(
    acp_server: str,
) -> tuple[str | None, str | None, list[str]]:
    """``(api_key_env_var, base_url_env_var, file_secret_names)`` for a server."""
    info = get_acp_provider(acp_server)
    if info is None:
        return None, None, []
    file_names = [spec.secret_name for spec in info.file_secrets]
    return info.api_key_env_var, info.base_url_env_var, file_names


def _build_openhands_settings(
    profile: OpenHandsAgentProfile,
    llm: LLM,
    mcp_config: dict[str, MCPServer],
    skills: list[Skill],
    base: AgentSettingsConfig | None,
) -> AgentSettingsConfig:
    """Compose ``OpenHandsAgentSettings`` from a profile and its resolved references.

    ``load_project_skills=True`` lets ``LocalConversation`` lazily load
    repo-scoped skills (no workspace exists yet), and ``disabled_skills`` rides
    the context so that lazy load honors the same deny-list.
    """
    context_fields: dict[str, Any] = {
        "skills": skills,
        "system_message_suffix": profile.system_message_suffix,
        "load_project_skills": True,
        "disabled_skills": profile.disabled_skills,
        "current_datetime": datetime.now().astimezone(),
    }
    fields: dict[str, Any] = {
        "agent": profile.agent,
        "llm": llm,
        "mcp_config": mcp_config,
        "tools": profile.tools,
        "condenser": profile.condenser,
        "enable_sub_agents": profile.enable_sub_agents,
        "enable_switch_llm_tool": profile.enable_switch_llm_tool,
        "tool_concurrency_limit": profile.tool_concurrency_limit,
    }
    if isinstance(base, OpenHandsAgentSettings):
        return base.model_copy(
            update={
                **fields,
                "agent_context": base.agent_context.model_copy(update=context_fields),
                "verification": base.verification.model_copy(
                    update=profile.verification.model_dump()
                ),
            }
        )
    return validate_agent_settings(
        {
            "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
            "agent_kind": "openhands",
            **fields,
            "agent_context": AgentContext(**context_fields),
            "verification": profile.verification.model_dump(),
        }
    )


def _build_acp_settings(
    profile: ACPAgentProfile,
    mcp_config: dict[str, MCPServer],
    skills: list[Skill],
    base: AgentSettingsConfig | None,
) -> AgentSettingsConfig:
    """Compose ``ACPAgentSettings`` from a profile.

    No credential is set — provider creds ride ``state.secret_registry``.
    Project skills stay off because the ACP CLI reads the repository itself
    (#4019), and ``current_datetime=None`` matches ACP's no-timestamp
    convention. A ``custom`` server has no default command.
    """
    command = shlex.split(profile.acp_command) if profile.acp_command else []
    if profile.acp_server == "custom" and not command:
        raise AgentLaunchError(
            "acp_command is required when acp_server='custom' — there is no "
            "default launch command to fall back to"
        )
    context_fields: dict[str, Any] = {
        "skills": skills,
        "current_datetime": None,
        "load_project_skills": False,
    }
    fields: dict[str, Any] = {
        "acp_server": profile.acp_server,
        "acp_model": profile.acp_model,
        "acp_session_mode": profile.acp_session_mode,
        "acp_prompt_timeout": profile.acp_prompt_timeout,
        "acp_startup_timeout": profile.acp_startup_timeout,
        "acp_command": command,
        "acp_args": list(profile.acp_args) if profile.acp_args else [],
        "mcp_config": mcp_config,
    }
    base_context = None if base is None else base.agent_context
    if base is not None and base.agent_kind == "acp":
        context = (
            base_context.model_copy(update=context_fields)
            if base_context is not None
            else AgentContext(**context_fields)
        )
        return base.model_copy(update={**fields, "agent_context": context})
    return validate_agent_settings(
        {
            "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
            "agent_kind": "acp",
            **fields,
            "agent_context": AgentContext(**context_fields),
        }
    )


def _load_llm(catalog: AgentLaunchCatalog, name: str) -> LLM | None:
    try:
        return catalog.llm_store.load(name, cipher=catalog.cipher)
    except FileNotFoundError:
        return None


def _resolve_settings(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    catalog: AgentLaunchCatalog,
    runtime: AgentLaunchRuntime,
    llm_profile_ref: str | None,
) -> AgentSettingsConfig:
    """Resolve a profile's references; every dangling one is reported at once."""
    mcp_config, _, dangling_mcp = _compute_mcp_filter(
        catalog.mcp_config, profile.mcp_server_refs
    )
    skills = _launch_skills(profile, catalog, runtime)
    if isinstance(profile, ACPAgentProfile):
        if dangling_mcp:
            raise UnresolvedProfileReferences(mcp_server_refs=dangling_mcp)
        return _build_acp_settings(profile, mcp_config, skills, catalog.base_settings)

    llm_ref = llm_profile_ref or profile.llm_profile_ref
    llm = _load_llm(catalog, llm_ref)
    if llm is None or dangling_mcp:
        raise UnresolvedProfileReferences(
            llm_profile_ref=llm_ref if llm is None else None,
            mcp_server_refs=dangling_mcp,
        )
    return _build_openhands_settings(
        profile, llm, mcp_config, skills, catalog.base_settings
    )


def _finish_context(
    context: AgentContext | None,
    *,
    is_acp: bool,
    runtime: AgentLaunchRuntime,
    additions: AgentLaunchAdditions | None,
) -> AgentContext | None:
    updates: dict[str, Any] = {}
    addition = (
        (additions.system_message_suffix_append or "").strip() if additions else ""
    )
    if addition:
        existing = ((context and context.system_message_suffix) or "").strip()
        updates["system_message_suffix"] = (
            f"{existing}\n\n{addition}" if existing else addition
        )
    if runtime.load_memory:
        updates["load_memory"] = True
    if (
        is_acp
        and not runtime.uses_skill_catalog("acp")
        and context is not None
        and (
            context.skills
            or context.load_user_skills
            or context.load_public_skills
            or context.registered_marketplaces
        )
    ):
        updates.update(
            skills=[],
            load_user_skills=False,
            load_public_skills=False,
            registered_marketplaces=[],
        )
    if not updates:
        return context
    # A missing context means "no prompt context", so none is synthesized with
    # a timestamp.
    base = context if context is not None else AgentContext(current_datetime=None)
    return base.model_copy(update=updates)


def _finish_settings(
    settings: AgentSettingsConfig,
    runtime: AgentLaunchRuntime,
    additions: AgentLaunchAdditions | None,
) -> AgentSettingsConfig:
    if not isinstance(settings, OpenHandsAgentSettings):
        return settings.model_copy(
            update={
                "agent_context": _finish_context(
                    settings.agent_context,
                    is_acp=True,
                    runtime=runtime,
                    additions=additions,
                )
            }
        )
    tools = settings.tools
    if tools is None:
        tools = default_tool_specs(
            enable_sub_agents=settings.enable_sub_agents,
            enable_browser=runtime.browser_available,
        )
    llm = settings.llm
    if runtime.stream:
        llm = llm.model_copy(update={"stream": True})
    return settings.model_copy(
        update={
            "tools": tools,
            "llm": llm,
            "agent_context": _finish_context(
                settings.agent_context,
                is_acp=False,
                runtime=runtime,
                additions=additions,
            ),
        }
    )


def _finish_agent(
    agent: AgentBase,
    runtime: AgentLaunchRuntime,
    additions: AgentLaunchAdditions | None,
) -> AgentBase:
    from openhands.sdk.agent.acp_agent import ACPAgent

    context = _finish_context(
        agent.agent_context,
        is_acp=isinstance(agent, ACPAgent),
        runtime=runtime,
        additions=additions,
    )
    if context is agent.agent_context:
        return agent
    return agent.model_copy(update={"agent_context": context})


def prepare_agent_launch(
    source: OpenHandsAgentProfile | ACPAgentProfile | AgentBase,
    *,
    catalog: AgentLaunchCatalog | None = None,
    runtime: AgentLaunchRuntime | None = None,
    additions: AgentLaunchAdditions | None = None,
    profile_origin: ProfileOrigin | None = "stored",
    build_agent: bool = True,
) -> AgentLaunchPlan:
    """Build the agent a conversation launches with.

    ``source`` is an Agent Profile (resolved against ``catalog``) or a raw
    agent, which only gets the runtime's memory/ACP-skill policy and
    ``additions`` applied. ``profile_origin`` selects the recorded provenance:
    ``None`` records none. ``build_agent=False`` skips ``create_agent()``, the
    only step with side effects (a subscription LLM refreshes its credentials).

    Raises:
        UnresolvedProfileReferences: the profile's LLM or MCP references dangle.
        AgentLaunchError: the launch inputs are inconsistent.
    """
    runtime = runtime or AgentLaunchRuntime()
    llm_override = additions.llm_profile_ref if additions else None

    if not isinstance(source, OpenHandsAgentProfile | ACPAgentProfile):
        if llm_override is not None:
            raise AgentLaunchError(
                "agent_launch_additions.llm_profile_ref requires an Agent Profile"
            )
        return AgentLaunchPlan(
            settings=None,
            agent=_finish_agent(source, runtime, additions),
            launched_profile=None,
            allowed_secrets=None,
        )

    if catalog is None:
        raise TypeError("An Agent Profile launch requires a catalog")
    if llm_override is not None and isinstance(source, ACPAgentProfile):
        raise AgentLaunchError(
            "agent_launch_additions.llm_profile_ref does not apply to ACP profiles"
        )

    settings = _finish_settings(
        _resolve_settings(source, catalog, runtime, llm_override),
        runtime,
        additions,
    )
    launched = None
    if profile_origin is not None:
        launched = LaunchedAgentProfile(
            agent_profile_id=source.id,
            revision=source.revision,
            secret_refs=source.secret_refs,
            inline=profile_origin == "inline",
            llm_profile_ref=llm_override,
        )
    return AgentLaunchPlan(
        settings=settings,
        agent=settings.create_agent() if build_agent else None,
        launched_profile=launched,
        allowed_secrets=(
            None if source.secret_refs is None else frozenset(source.secret_refs)
        ),
    )


@dataclass(frozen=True)
class _FixedLLMLoader:
    name: str
    llm: LLM

    def load(self, name: str, *, cipher: Cipher | None = None) -> LLM:  # noqa: ARG002
        if name != self.name:
            raise FileNotFoundError(name)
        return self.llm


_AGENT_SETTINGS_PROFILE_NAME = "agent_settings"


def agent_settings_launch_source(
    settings: AgentSettingsConfig,
) -> tuple[OpenHandsAgentProfile | ACPAgentProfile, AgentLaunchCatalog]:
    """Convert a deprecated ``agent_settings`` launch into an inline profile.

    The payload's own LLM, MCP servers and skills become the catalog, so the
    launch resolves exactly what the client sent.
    """
    profile = build_seed_profile(
        settings, _AGENT_SETTINGS_PROFILE_NAME, name=_AGENT_SETTINGS_PROFILE_NAME
    )
    context = settings.agent_context
    if isinstance(profile, OpenHandsAgentProfile) and context is not None:
        profile = profile.model_copy(
            update={"disabled_skills": list(context.disabled_skills)}
        )
    catalog = AgentLaunchCatalog(
        llm_store=_FixedLLMLoader(_AGENT_SETTINGS_PROFILE_NAME, settings.llm),
        mcp_config=settings.mcp_config,
        skills=list(context.skills) if context is not None else [],
        base_settings=settings,
    )
    return profile, catalog


def resolve_agent_profile(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    *,
    llm_store: LLMProfileLoader,
    mcp_config: dict[str, MCPServer],
    available_skills: list[Skill] | None,
    cipher: Cipher | None = None,
) -> AgentSettingsConfig:
    """Resolve a profile's references into a validated ``AgentSettingsConfig``.

    The reference-resolution step of :func:`prepare_agent_launch`, without the
    runtime and per-launch pieces. ``available_skills`` is the skill catalog the
    agent gets (minus ``disabled_skills`` for an OpenHands profile).

    Raises:
        ProfileNotFound: ``llm_profile_ref`` does not exist (OpenHands path).
        DanglingMcpServerRef: an ``mcp_server_refs`` entry is not in ``mcp_config``.
    """
    catalog = AgentLaunchCatalog(
        llm_store=llm_store,
        mcp_config=mcp_config,
        skills=available_skills,
        cipher=cipher,
    )
    runtime = AgentLaunchRuntime(acp_skill_sourcing="openhands_managed")
    try:
        return _resolve_settings(profile, catalog, runtime, None)
    except UnresolvedProfileReferences as e:
        if e.mcp_server_refs:
            raise DanglingMcpServerRef(e.mcp_server_refs) from e
        raise ProfileNotFound(f"LLM profile {e.llm_profile_ref!r} not found") from e


def resolve_agent_profile_dry_run(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    *,
    llm_store: LLMProfileLoader,
    mcp_config: dict[str, MCPServer],
    available_skills: list[Skill] | None,
    cipher: Cipher | None = None,
    runtime: AgentLaunchRuntime | None = None,
    additions: AgentLaunchAdditions | None = None,
) -> AgentProfileDiagnostics:
    """Report what :func:`prepare_agent_launch` would build, without raising.

    Runs the launch with ``build_agent=False``. ``runtime`` defaults to one that
    gives an ACP profile the supplied ``available_skills``.
    """
    runtime = runtime or AgentLaunchRuntime(acp_skill_sourcing="openhands_managed")
    catalog = AgentLaunchCatalog(
        llm_store=llm_store,
        mcp_config=mcp_config,
        skills=available_skills,
        cipher=cipher,
    )
    _, resolved_mcp, dangling_mcp = _compute_mcp_filter(
        mcp_config, profile.mcp_server_refs
    )
    diagnostics = AgentProfileDiagnostics(
        agent_kind=profile.agent_kind,
        mcp_server_refs=profile.mcp_server_refs,
        resolved_mcp_config_keys=resolved_mcp,
        dangling_mcp_server_refs=dangling_mcp,
        secret_refs=profile.secret_refs,
        resolved_skills=[s.name for s in _launch_skills(profile, catalog, runtime)],
    )

    if isinstance(profile, OpenHandsAgentProfile):
        diagnostics.disabled_skills = profile.disabled_skills
        llm_ref = (additions and additions.llm_profile_ref) or profile.llm_profile_ref
        diagnostics.llm_profile_ref = llm_ref
        try:
            llm = _load_llm(catalog, llm_ref)
        except Exception as e:
            # The store can raise lock timeouts or validation errors; keep the
            # preview total.
            diagnostics.errors.append(f"Could not load LLM profile {llm_ref!r}: {e}")
        else:
            diagnostics.llm_profile_resolved = llm is not None
            diagnostics.llm_api_key_set = llm is not None and _api_key_set(llm)
    else:
        (
            diagnostics.acp_api_key_secret_name,
            diagnostics.acp_base_url_secret_name,
            diagnostics.acp_file_secret_names,
        ) = _acp_credential_channels(profile.acp_server)

    if not diagnostics.errors:
        try:
            plan = prepare_agent_launch(
                profile,
                catalog=catalog,
                runtime=runtime,
                additions=additions,
                build_agent=False,
            )
        except UnresolvedProfileReferences as e:
            if e.mcp_server_refs:
                diagnostics.errors.append(
                    "MCP server(s) not configured: " + ", ".join(e.mcp_server_refs)
                )
            if e.llm_profile_ref is not None:
                diagnostics.errors.append(
                    f"LLM profile {e.llm_profile_ref!r} not found"
                )
        except Exception as e:
            diagnostics.errors.append(f"Failed to build agent settings: {e}")
        else:
            if plan.settings is not None:
                # No expose context => secrets redacted (mcp env/headers, api_key).
                diagnostics.resolved_settings = plan.settings.model_dump(mode="json")

    diagnostics.valid = not diagnostics.errors
    return diagnostics
