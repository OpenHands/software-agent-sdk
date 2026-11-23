"""Profile resolution building blocks shared with :mod:`openhands.sdk.launch`.

The legacy ``resolve_agent_profile()`` / ``resolve_agent_profile_dry_run()``
entry points were removed in 1.60.0; callers use
``openhands.sdk.launch.resolve`` and ``preview_launch``. This module keeps the
shared composition helpers and :class:`AgentProfileDiagnostics`.

A profile carries *references* (``llm_profile_ref`` / ``mcp_server_refs``) plus a
``disabled_skills`` deny-list, and is secret-free at rest; an
:data:`~openhands.sdk.settings.model.AgentSettingsConfig` embeds the resolved
``llm`` / ``mcp_config`` / skills. This module resolves the former into the
latter so ``create_agent`` / ``apply_agent_settings_diff`` /
``validate_agent_settings`` stay unchanged. See epic #3713.

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
from collections.abc import Collection, Container, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, SecretStr

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.llm.meta_profile_store import MetaProfile
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles.agent_profile import (
    ACPAgentProfile,
    OpenHandsAgentProfile,
)
from openhands.sdk.settings.acp_providers import get_acp_provider
from openhands.sdk.settings.model import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    AgentSettingsConfig,
    validate_agent_settings,
)
from openhands.sdk.skills import Skill
from openhands.sdk.subagent.scope import SubAgentScope, scope_delegation_tools
from openhands.sdk.tool.defaults import BROWSER_TOOL_NAME, launch_tool_specs
from openhands.sdk.tool.registry import is_tool_available
from openhands.sdk.tool.spec import Tool
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM


class ProfileNotFound(Exception):
    """A referenced profile (e.g. ``llm_profile_ref``) does not exist.

    The router (#3719) maps this to HTTP 404.
    """


class AgentProfileDiagnostics(BaseModel):
    """Side-effect-free report of what a launch would do.

    Consumed by ``POST /{id}/materialize`` (#3719) and the canvas editor. The
    verdict (:attr:`valid`) and the dangling-ref lists match exactly what a real
    resolve produces; :attr:`resolved_settings` is the redacted settings dump
    (present only when :attr:`valid`).
    """

    agent_kind: str
    valid: bool = False
    errors: list[str] = Field(default_factory=list)
    unusable_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Selected tools the runtime cannot run. An unavailable browser is "
            "left out of the launch; any other such tool fails the launch or "
            "fails when the agent uses it."
        ),
    )

    # OpenHands LLM reference.
    llm_profile_ref: str | None = None
    llm_profile_resolved: bool = False
    llm_api_key_set: bool = False

    # MCP composition (both variants).
    mcp_server_refs: list[str] | None = None
    resolved_mcp_config_keys: list[str] = Field(default_factory=list)
    dangling_mcp_server_refs: list[str] = Field(default_factory=list)

    # Skill selection (OpenHands only). Unlike ``mcp_server_refs`` it can never
    # dangle: a name absent from the catalog is a harmless no-op.
    # ``resolved_skills`` is what would actually reach the agent.
    disabled_skills: list[str] = Field(default_factory=list)
    public_skills: list[str] | None = None
    resolved_skills: list[str] = Field(default_factory=list)

    # Secret scope (both variants). ``None`` = every secret the conversation is
    # started with; a list = only those names, with nothing added back.
    # No dangling report: this is an
    # allow-list over what a launch supplies, so an unmatched name is a no-op.
    secret_refs: list[str] | None = None
    # Built-in integration scope (both variants); same allow-list semantics.
    integration_refs: list[str] | None = None

    # ACP provider credential channels the editor/materialize checks (ACP only).
    # These are NOT jointly required: authentication needs the API key *or* one
    # of the file-content credentials, and the base URL is optional proxy
    # routing. Keeping them in separate fields lets the editor mark set/missing
    # honestly instead of treating a working api-key-only setup as incomplete.
    acp_api_key_secret_name: str | None = None
    acp_base_url_secret_name: str | None = None
    acp_file_secret_names: list[str] = Field(default_factory=list)

    # Meta-profile routing (OpenHands only).
    meta_profile_ref: str | None = None
    dangling_meta_profile_ref: str | None = None
    dangling_meta_profile_llm_refs: list[str] = Field(default_factory=list)

    # Redacted resolved settings, present iff ``valid``.
    resolved_settings: dict[str, Any] | None = None


def _server_names(mcp_config: dict[str, MCPServer]) -> list[str]:
    return list(mcp_config)


def _partition_refs(
    refs: list[str], available: Container[str]
) -> tuple[list[str], list[str]]:
    """Split ``refs`` into ``(resolved, dangling)`` by membership in ``available``.

    Order-preserving and de-duplicated: a name repeated in ``refs`` is kept once,
    in first position. Shared by the MCP and skill filters so both partition
    identically — in particular both collapse duplicate refs, which the ACP skill
    path needs (``AgentContext`` rejects duplicate skill names).
    """
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
    mcp_config: dict[str, MCPServer],
    refs: list[str] | None,
) -> tuple[dict[str, MCPServer], list[str], list[str]]:
    """Resolve ``mcp_server_refs`` against the user's ``mcp_config``.

    ``None`` → passthrough (all servers); a non-null list filters to the named
    keys. Returns ``(filtered_servers, resolved_names, dangling_names)``.
    """
    if refs is None:
        return mcp_config, _server_names(mcp_config), []
    resolved, dangling = _partition_refs(refs, mcp_config)
    return {k: mcp_config[k] for k in resolved}, resolved, dangling


def _select_skills(
    available_skills: Sequence[Skill] | None,
    disabled: Collection[str],
    public_skills: Collection[str] | None = None,
    public_skill_names: Collection[str] | None = None,
) -> list[Skill]:
    """Filter the discovered skill catalog by a profile's skill selection.

    ``disabled`` is a deny-list over every skill. ``public_skills`` is an
    allow-list over the public tier only (``None`` = the whole tier), so a user
    skill stays on the moment it appears. ``public_skill_names`` names that
    tier; ``None`` counts every catalog skill as public. Names absent from the
    catalog are no-ops, so neither list can fail a launch when the catalog
    drifts (#4017).

    The catalog is de-duplicated by name (last occurrence wins, matching
    ``load_all_skills``'s later-source-overrides precedence) so a colliding
    catalog cannot trip ``AgentContext``'s duplicate-name validator. An empty or
    ``None`` catalog yields no skills.
    """
    if not available_skills:
        return []
    by_name = {s.name: s for s in available_skills}
    denied = set(disabled)
    allowed = None if public_skills is None else set(public_skills)

    def keep(name: str) -> bool:
        if name in denied:
            return False
        if allowed is None or name in allowed:
            return True
        return public_skill_names is not None and name not in public_skill_names

    return [s for s in by_name.values() if keep(s.name)]


def _profile_skills(
    profile: OpenHandsAgentProfile,
    available_skills: Sequence[Skill] | None,
    public_skill_names: Collection[str] | None,
) -> list[Skill]:
    return _select_skills(
        available_skills,
        profile.disabled_skills,
        profile.public_skills,
        public_skill_names,
    )


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
    """Provider credential channels for ``acp_server`` via ``ACP_PROVIDERS``.

    Returns ``(api_key_env_var, base_url_env_var, file_secret_names)`` kept
    separate by role: the API-key env var and the file-content credentials are
    *alternative* auth mechanisms (one suffices), and the base URL is optional
    proxy routing — not jointly required. All empty/``None`` for ``'custom'``
    servers, whose creds the user manages directly.
    """
    info = get_acp_provider(acp_server)
    if info is None:
        return None, None, []
    file_names = [spec.secret_name for spec in info.file_secrets]
    return info.api_key_env_var, info.base_url_env_var, file_names


def _build_openhands_settings(
    profile: OpenHandsAgentProfile,
    llm: LLM,
    mcp_config: dict[str, MCPServer],
    filtered_skills: list[Skill],
    *,
    browser_available: bool | None,
    meta_profile: MetaProfile | None = None,
    meta_profile_llms: Mapping[str, LLM] | None = None,
    critic_api_key: SecretStr | None = None,
) -> AgentSettingsConfig:
    """Compose the resolved ``OpenHandsAgentSettings`` from a profile + LLM.

    ``filtered_skills`` (the discovered catalog minus ``disabled_skills``) is the
    sole user/public skill source (profiles no longer embed skills).
    ``load_project_skills=True`` lets ``LocalConversation`` lazily load
    repo-scoped project skills, which can't be resolved here (no workspace yet);
    ``disabled_skills`` is carried onto the context so that lazy load applies the
    same deny-list. ``load_user_skills`` / ``load_public_skills`` stay False on
    purpose: user/public skills already arrive via ``filtered_skills``, so
    enabling the flags would double-load them.
    """
    payload = {
        "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
        "agent_kind": "openhands",
        "agent": profile.agent,
        "llm": llm,
        "mcp_config": mcp_config,
        "tools": _scope_sub_agents(
            profile,
            profile.tools
            if browser_available is None
            else launch_tool_specs(profile.tools, browser_available=browser_available),
        ),
        "persona": profile.persona,
        "agent_context": AgentContext(
            skills=filtered_skills,
            system_message_suffix=profile.system_message_suffix,
            load_project_skills=True,
            disabled_skills=profile.disabled_skills,
        ),
        "condenser": profile.condenser,
        "verification": {
            **profile.verification.model_dump(),
            "critic_api_key": critic_api_key,
        },
        "enable_classify_and_switch_llm_tool": (
            profile.enable_classify_and_switch_llm_tool
        ),
        "active_meta_profile": profile.meta_profile_ref,
        "meta_profile": meta_profile,
        "meta_profile_llms": dict(meta_profile_llms or {}),
        "tool_concurrency_limit": profile.tool_concurrency_limit,
    }
    return validate_agent_settings(payload)


def _scope_sub_agents(
    profile: OpenHandsAgentProfile, tools: list[Tool] | None
) -> list[Tool] | None:
    if tools is None:
        return None
    return scope_delegation_tools(tools, SubAgentScope(tools=profile.tools is not None))


def _unusable_tools(
    tools: list[Tool] | None, *, browser_available: bool | None, check_usable: bool
) -> list[str]:
    if tools is None:
        return [] if browser_available is not False else [BROWSER_TOOL_NAME]
    return [
        tool.name
        for tool in tools
        if not (
            browser_available is not False
            if tool.name == BROWSER_TOOL_NAME
            else is_tool_available(tool.name, check_usable=check_usable)
        )
    ]


def _build_acp_settings(
    profile: ACPAgentProfile,
    mcp_config: dict[str, MCPServer],
    managed_skills: list[Skill],
) -> AgentSettingsConfig:
    """Compose the resolved ``ACPAgentSettings`` from a profile.

    ``acp_command`` is stored as a shell string and split into the settings'
    token list. No credential is set — provider creds ride
    ``state.secret_registry``.

    ``load_project_skills`` stays ``False``: an ACP CLI reads ``AGENTS.md`` /
    ``CLAUDE.md`` and its own project skills from the session cwd, so loading
    them here would duplicate that content in the prompt (#4019).
    ``managed_skills`` is what the *deployment* chose to inject — empty when the
    ACP CLI can reach its own host configuration, non-empty in a container where
    it cannot. ``current_datetime=None`` matches ACP's no-timestamp convention.
    A ``custom`` server has no default command, so one must be supplied.
    """
    command = shlex.split(profile.acp_command) if profile.acp_command else []
    if profile.acp_server == "custom" and not command:
        raise ValueError(
            "acp_command is required when acp_server='custom' — there is no "
            "default launch command to fall back to"
        )
    agent_context = AgentContext(
        skills=managed_skills, current_datetime=None, load_project_skills=False
    )
    payload = {
        "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
        "agent_kind": "acp",
        "acp_server": profile.acp_server,
        "acp_model": profile.acp_model,
        "acp_effort": profile.acp_effort,
        "acp_session_mode": profile.acp_session_mode,
        "acp_prompt_timeout": profile.acp_prompt_timeout,
        "acp_startup_timeout": profile.acp_startup_timeout,
        "acp_command": command,
        "acp_args": list(profile.acp_args) if profile.acp_args else [],
        "mcp_config": mcp_config,
        "agent_context": agent_context,
    }
    return validate_agent_settings(payload)
