"""``resolve_agent_profile()`` — the join point between profiles and execution.

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
from collections.abc import Container, Mapping
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
    CondenserSettingsConfig,
    LLMSummarizingCondenserSettings,
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
    from openhands.sdk.llm.llm_profile_store import LLMProfileLoader
    from openhands.sdk.utils.cipher import Cipher


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


class AgentProfileDiagnostics(BaseModel):
    """Side-effect-free report of what :func:`resolve_agent_profile` would do.

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

    # Condenser LLM reference (#5469): the dedicated summarization profile,
    # when the condenser settings name one.
    condenser_llm_profile_ref: str | None = None
    condenser_llm_profile_resolved: bool = False

    # MCP composition (both variants).
    mcp_server_refs: list[str] | None = None
    resolved_mcp_config_keys: list[str] = Field(default_factory=list)
    dangling_mcp_server_refs: list[str] = Field(default_factory=list)

    # Skill selection (OpenHands only). ``disabled_skills`` is a deny-list over
    # the discovered catalog, so — unlike ``mcp_server_refs`` — it can never
    # dangle: a disabled name absent from the catalog is a harmless no-op.
    # ``resolved_skills`` is what would actually reach the agent (catalog minus
    # disabled).
    disabled_skills: list[str] = Field(default_factory=list)
    resolved_skills: list[str] = Field(default_factory=list)

    # Secret scope (both variants). ``None`` = every secret the conversation is
    # started with; a list = only those names, with nothing added back.
    # No dangling report: this is an
    # allow-list over what a launch supplies, so an unmatched name is a no-op.
    secret_refs: list[str] | None = None

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


def _apply_disabled_skills(
    available_skills: list[Skill] | None,
    disabled: list[str],
) -> list[Skill]:
    """Filter the discovered skill catalog by the profile's deny-list.

    Skills are discovered from many incomplete, drifting sources, so selection
    is by *exclusion*, not by an allow-list of names (which would dangle when the
    catalog a profile was authored against differs from the one resolved at
    launch — the #4017 root cause). A disabled name absent from the catalog is a
    harmless no-op.

    The catalog is de-duplicated by name (last occurrence wins, matching
    ``load_all_skills``'s later-source-overrides precedence) so a caller passing a
    colliding catalog — the app-server's "fuller catalog" merges multiple sources
    whose names can collide — cannot trip ``AgentContext``'s duplicate-name
    validator. So this can never raise.

    ``available_skills is None`` (discovery skipped or failed) → no skills, the
    caller having surfaced its own signal. ``disabled == []`` → the whole
    (de-duplicated) catalog.
    """
    if not available_skills:
        return []
    by_name = {s.name: s for s in available_skills}
    denied = set(disabled)
    return [s for s in by_name.values() if s.name not in denied]


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
    condenser_llm: LLM | None = None,
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

    ``condenser_llm`` is the resolved LLM named by
    ``profile.condenser.llm_profile_ref`` (the dedicated summarization model),
    when the profile asks for one; it is embedded into the condenser settings so
    the resolved settings stay store-free, mirroring ``meta_profile_llms``.
    """
    condenser = profile.condenser
    condenser_ref = condenser_llm_profile_ref(condenser)
    if condenser_llm is not None and condenser_ref is not None:
        # Rebuild, don't mutate: profile objects are caller-owned and Pydantic
        # models validate the whole tree on assignment anyway. The embedded LLM's
        # usage_id names the store key so switch_llm can recognize it later.
        condenser = condenser.model_copy(
            update={"llm": condenser_llm.model_copy(update={"usage_id": condenser_ref})}
        )
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
        "condenser": condenser,
        "verification": profile.verification.model_dump(),
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
    scope = SubAgentScope(
        tools=profile.tools is not None,
        mcp_servers=profile.mcp_server_refs is not None,
    )
    return scope_delegation_tools(tools, scope)


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
        "acp_session_mode": profile.acp_session_mode,
        "acp_prompt_timeout": profile.acp_prompt_timeout,
        "acp_startup_timeout": profile.acp_startup_timeout,
        "acp_command": command,
        "acp_args": list(profile.acp_args) if profile.acp_args else [],
        "mcp_config": mcp_config,
        "agent_context": agent_context,
    }
    return validate_agent_settings(payload)


def resolve_agent_profile(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    *,
    llm_store: LLMProfileLoader,
    mcp_config: dict[str, MCPServer],
    available_skills: list[Skill] | None,
    cipher: Cipher | None = None,
    browser_available: bool | None = None,
) -> AgentSettingsConfig:
    """Resolve a profile's references into a validated ``AgentSettingsConfig``.

    ``mcp_config`` is the user's globally-configured MCP server map, already
    decrypted by the caller (the agent-server runs settings decryption
    before calling). ``available_skills`` is the server-discovered skill catalog
    (the agent-server caller passes the result of ``load_all_skills``); an
    OpenHands profile keeps all of it except the names in ``disabled_skills``,
    and an ACP profile keeps all of it (it has no deny-list). ``None`` means the
    caller injected no catalog — discovery was not run, failed, or, for ACP, the
    deployment leaves skill sourcing to the CLI. Unlike the ``mcp_server_refs``
    allow-list, the ``disabled_skills`` deny-list can never dangle, so this
    never raises for skills. ``cipher`` decrypts the referenced LLM profile.
    ``browser_available`` says whether the runtime the agent will run on can
    use the browser tool set; the caller probes it. ``None`` leaves ``tools``
    as the profile stores them.

    Raises:
        ProfileNotFound: ``llm_profile_ref`` does not exist (OpenHands path).
        DanglingMcpServerRef: an ``mcp_server_refs`` entry is not in ``mcp_config``.
    """
    filtered_mcp, _, dangling = _compute_mcp_filter(mcp_config, profile.mcp_server_refs)
    if dangling:
        raise DanglingMcpServerRef(dangling)

    if isinstance(profile, OpenHandsAgentProfile):
        filtered_skills = _apply_disabled_skills(
            available_skills, profile.disabled_skills
        )
        try:
            llm = llm_store.load(profile.llm_profile_ref, cipher=cipher)
        except FileNotFoundError as e:
            raise ProfileNotFound(
                f"LLM profile {profile.llm_profile_ref!r} not found"
            ) from e
        condenser_llm = _load_condenser_llm(profile, llm_store, cipher=cipher)
        return _build_openhands_settings(
            profile,
            llm,
            filtered_mcp,
            filtered_skills,
            browser_available=browser_available,
            condenser_llm=condenser_llm,
        )

    return _build_acp_settings(
        profile, filtered_mcp, _apply_disabled_skills(available_skills, [])
    )


def condenser_llm_profile_ref(
    condenser: CondenserSettingsConfig | object,
) -> str | None:
    """The LLM profile a condenser settings object asks for, if any.

    Only ``LLMSummarizingCondenserSettings`` carries the field; the union's
    other variants condense without an LLM. The ``| object`` keeps the helper
    usable from the ``AgentProfile.condenser`` union without a cast.
    """
    if not isinstance(condenser, LLMSummarizingCondenserSettings):
        return None
    return condenser.llm_profile_ref or None


def _load_condenser_llm(
    profile: OpenHandsAgentProfile,
    llm_store: LLMProfileLoader,
    *,
    cipher: Cipher | None = None,
) -> LLM | None:
    """Resolve ``condenser.llm_profile_ref`` against the LLM profile store.

    Returns ``None`` when the profile's condenser names no dedicated LLM (or
    names the agent's own profile, which the condenser already follows). A
    dangling ref raises ``ProfileNotFound`` — same contract as ``llm_profile_ref``.
    """
    ref = condenser_llm_profile_ref(profile.condenser)
    if not ref or ref == profile.llm_profile_ref:
        return None
    try:
        return llm_store.load(ref, cipher=cipher)
    except FileNotFoundError as e:
        raise ProfileNotFound(f"LLM profile {ref!r} not found") from e


def resolve_agent_profile_dry_run(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    *,
    llm_store: LLMProfileLoader,
    mcp_config: dict[str, MCPServer],
    available_skills: list[Skill] | None,
    cipher: Cipher | None = None,
    browser_available: bool | None = None,
    check_usable: bool = True,
) -> AgentProfileDiagnostics:
    """Compute :class:`AgentProfileDiagnostics` without raising or side effects.

    Mirrors :func:`resolve_agent_profile`'s composition but records dangling LLM /
    MCP refs as diagnostics instead of raising, so the editor / ``/materialize``
    (#3719) can show a faithful set/missing report with secrets redacted. Skills
    use a deny-list (``disabled_skills``) that can't dangle, so there is no skill
    error to report — ``resolved_skills`` is just the catalog minus the disabled
    names. ``available_skills=None`` (discovery skipped or failed) means no
    user/public skills resolve.

    ``check_usable=False`` skips the usability probes, for runtimes this
    process cannot probe.
    """
    filtered_mcp, resolved, dangling = _compute_mcp_filter(
        mcp_config, profile.mcp_server_refs
    )
    diagnostics = AgentProfileDiagnostics(
        agent_kind=profile.agent_kind,
        mcp_server_refs=profile.mcp_server_refs,
        resolved_mcp_config_keys=resolved,
        dangling_mcp_server_refs=dangling,
        secret_refs=profile.secret_refs,
    )
    if dangling:
        diagnostics.errors.append(
            "MCP server(s) not configured: " + ", ".join(dangling)
        )

    # Skill selection report. Deny-list semantics: the catalog minus disabled
    # names, never dangling. An ACP profile has no deny-list of its own — its
    # catalog is whatever the deployment injects (empty unless the caller passes
    # one), and never includes project skills (#4019).
    if isinstance(profile, OpenHandsAgentProfile):
        filtered_skills = _apply_disabled_skills(
            available_skills, profile.disabled_skills
        )
        diagnostics.disabled_skills = profile.disabled_skills
        diagnostics.unusable_tools = _unusable_tools(
            profile.tools,
            browser_available=browser_available,
            check_usable=check_usable,
        )
        failing = [n for n in diagnostics.unusable_tools if n != BROWSER_TOOL_NAME]
        if failing:
            diagnostics.errors.append(
                "Tool(s) this server cannot run: " + ", ".join(failing)
            )
    else:
        filtered_skills = _apply_disabled_skills(available_skills, [])
    diagnostics.resolved_skills = [s.name for s in filtered_skills]

    llm: LLM | None = None
    if isinstance(profile, OpenHandsAgentProfile):
        diagnostics.llm_profile_ref = profile.llm_profile_ref
        try:
            llm = llm_store.load(profile.llm_profile_ref, cipher=cipher)
            diagnostics.llm_profile_resolved = True
            diagnostics.llm_api_key_set = _api_key_set(llm)
        except FileNotFoundError:
            diagnostics.errors.append(
                f"LLM profile {profile.llm_profile_ref!r} not found"
            )
        except Exception as e:
            # Keep the dry-run total: the store can raise filelock.TimeoutError
            # (lock contention), OSError, or a validation error before its own
            # handler runs. Surface those as a diagnostic instead of crashing
            # the editor preview (#3719) — distinct from a definitively-missing
            # profile above.
            diagnostics.errors.append(
                f"Could not load LLM profile {profile.llm_profile_ref!r}: {e}"
            )
        # Condenser LLM reference (#5469). Same resolution rules as
        # resolve_agent_profile: unset or equal to the agent ref -> no-op.
        condenser_ref = condenser_llm_profile_ref(profile.condenser)
        condenser_llm: LLM | None = None
        if condenser_ref and condenser_ref != profile.llm_profile_ref:
            diagnostics.condenser_llm_profile_ref = condenser_ref
            try:
                condenser_llm = llm_store.load(condenser_ref, cipher=cipher)
                diagnostics.condenser_llm_profile_resolved = True
            except FileNotFoundError:
                diagnostics.errors.append(f"LLM profile {condenser_ref!r} not found")
            except Exception as e:
                diagnostics.errors.append(
                    f"Could not load LLM profile {condenser_ref!r}: {e}"
                )
    else:
        condenser_llm = None
        (
            diagnostics.acp_api_key_secret_name,
            diagnostics.acp_base_url_secret_name,
            diagnostics.acp_file_secret_names,
        ) = _acp_credential_channels(profile.acp_server)

    diagnostics.valid = not diagnostics.errors
    if diagnostics.valid:
        # Building settings can still fail on input that passes profile
        # validation (e.g. an acp_command with unbalanced shell quotes, which
        # shlex.split rejects). Keep the dry-run total: surface such failures as
        # diagnostics rather than raising, matching the API contract.
        try:
            if isinstance(profile, OpenHandsAgentProfile):
                # valid here implies the LLM load above succeeded; gate
                # explicitly rather than via assert (stripped under python -O).
                if llm is None:
                    raise RuntimeError(
                        "OpenHands profile marked valid without a resolved LLM"
                    )
                settings = _build_openhands_settings(
                    profile,
                    llm,
                    filtered_mcp,
                    filtered_skills,
                    browser_available=browser_available,
                    condenser_llm=condenser_llm,
                )
            else:
                settings = _build_acp_settings(profile, filtered_mcp, filtered_skills)
            # No expose context => secrets redacted (mcp env/headers, llm api_key).
            diagnostics.resolved_settings = settings.model_dump(mode="json")
        except Exception as e:
            diagnostics.valid = False
            diagnostics.errors.append(f"Failed to build agent settings: {e}")

    return diagnostics
