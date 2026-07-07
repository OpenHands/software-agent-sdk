"""``resolve_agent_profile()`` — the join point between profiles and execution.

A profile carries *references* (``llm_profile_ref`` / ``mcp_server_refs`` /
``skill_refs``) and is secret-free at rest; an
:data:`~openhands.sdk.settings.model.AgentSettingsConfig` embeds the resolved
``llm`` / ``mcp_config`` / skills. This module resolves the former into the
latter so ``create_agent`` / ``apply_agent_settings_diff`` /
``validate_agent_settings`` stay unchanged. See epic #3713.

``skill_refs`` is a name filter over the server-discovered skill catalog,
mirroring how ``mcp_server_refs`` filters ``mcp_config``: the caller passes the
discovered skills (``load_all_skills``) and the resolver selects by name, so the
client never has to round-trip full ``Skill`` objects (#3868).

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
from collections.abc import Container
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, SecretStr

from openhands.sdk.context.agent_context import AgentContext
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


class DanglingSkillRef(Exception):
    """A ``skill_refs`` entry names a skill absent from the discovered catalog.

    The skill analogue of :class:`DanglingMcpServerRef`: the router maps it to
    HTTP 422. :attr:`missing` carries the offending name(s).
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        joined = ", ".join(repr(m) for m in missing)
        super().__init__(
            f"Skill ref(s) not present in the discovered catalog: {joined}"
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

    # OpenHands LLM reference.
    llm_profile_ref: str | None = None
    llm_profile_resolved: bool = False
    llm_api_key_set: bool = False

    # MCP composition (both variants).
    mcp_server_refs: list[str] | None = None
    resolved_mcp_config_keys: list[str] = Field(default_factory=list)
    resolved_mcp_servers: list[str] = Field(
        default_factory=list,
        description="Deprecated alias for resolved_mcp_config_keys.",
    )
    dangling_mcp_server_refs: list[str] = Field(default_factory=list)

    # Skill selection (both variants). A dangling skill ref flips ``valid`` via
    # an error, mirroring ``dangling_mcp_server_refs`` (see #3868 / Option A).
    skill_refs: list[str] | None = None
    resolved_skills: list[str] = Field(default_factory=list)
    dangling_skill_refs: list[str] = Field(default_factory=list)

    # ACP provider credential channels the editor/materialize checks (ACP only).
    # These are NOT jointly required: authentication needs the API key *or* one
    # of the file-content credentials, and the base URL is optional proxy
    # routing. Keeping them in separate fields lets the editor mark set/missing
    # honestly instead of treating a working api-key-only setup as incomplete.
    acp_api_key_secret_name: str | None = None
    acp_base_url_secret_name: str | None = None
    acp_file_secret_names: list[str] = Field(default_factory=list)

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


def _compute_skill_filter(
    available_skills: list[Skill] | None,
    refs: list[str] | None,
) -> tuple[list[Skill], list[str], list[str]]:
    """Resolve ``skill_refs`` against the server-discovered skill catalog.

    Mirrors :func:`_compute_mcp_filter`. ``refs is None`` selects all discovered
    skills; a list filters to the named skills in ref order (duplicates
    collapsed). Returns ``(filtered_skills, resolved_names, dangling_names)``.

    ``available_skills is None`` means discovery was not run (e.g. an empty
    ``skill_refs`` skips it) or failed: the catalog is unknown, so refs are
    neither resolved nor reported dangling and the caller surfaces its own
    signal. ``available_skills == []`` is a *known-empty* catalog, against which
    any named ref is dangling.
    """
    if available_skills is None:
        return [], [], []
    by_name = {s.name: s for s in available_skills}
    if refs is None:
        return list(by_name.values()), list(by_name), []
    resolved, dangling = _partition_refs(refs, by_name)
    return [by_name[r] for r in resolved], resolved, dangling


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
) -> AgentSettingsConfig:
    """Compose the resolved ``OpenHandsAgentSettings`` from a profile + LLM.

    ``filtered_skills`` (the ``skill_refs`` selection over the server-discovered
    catalog) is the sole skill source — profiles no longer embed ``skills``
    (#4017). ``load_project_skills=True`` restores the one enrichment the
    catalog can't provide: project skills are repo-scoped, not resolvable at
    profile-resolve time (no workspace yet), so ``LocalConversation`` loads them
    lazily on first use, the same way an ``agent_settings``-launched
    conversation already does (#4016). ``load_user_skills`` /
    ``load_public_skills`` are deliberately left at their default ``False`` —
    unlike project skills, user/public skills already reach the agent through
    ``filtered_skills`` (the resolver's catalog, via ``discover_profile_skills``,
    already includes them); flipping those flags too would trigger a second,
    redundant discovery pass inside ``AgentContext`` itself.
    """
    payload = {
        "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
        "agent_kind": "openhands",
        "agent": profile.agent,
        "llm": llm,
        "mcp_config": mcp_config,
        # Tri-state passthrough; create_agent materializes None.
        "tools": profile.tools,
        "agent_context": AgentContext(
            skills=filtered_skills,
            system_message_suffix=profile.system_message_suffix,
            load_project_skills=True,
        ),
        "condenser": profile.condenser,
        "verification": profile.verification.model_dump(),
        "enable_sub_agents": profile.enable_sub_agents,
        "enable_switch_llm_tool": profile.enable_switch_llm_tool,
        "tool_concurrency_limit": profile.tool_concurrency_limit,
    }
    return validate_agent_settings(payload)


def _build_acp_settings(
    profile: ACPAgentProfile,
    mcp_config: dict[str, MCPServer],
    filtered_skills: list[Skill],
) -> AgentSettingsConfig:
    """Compose the resolved ``ACPAgentSettings`` from a profile.

    ``acp_command`` is stored as a shell string and split into the settings'
    token list. No credential is set — provider creds ride
    ``state.secret_registry`` (#3720). ``filtered_skills`` (the ``skill_refs``
    selection, ``[]`` by default for ACP profiles) become prompt-only context
    via ``agent_context``; ``current_datetime=None`` matches ACP's convention of
    not injecting a timestamp. ``agent_context`` is always constructed (never
    ``None``, even with no selected skills) so ``load_project_skills=True``
    still reaches ``LocalConversation``'s lazy project-skill load (#4016) —
    project skills are repo-scoped, not gated by ``skill_refs``, and apply to
    ACP profiles the same as OpenHands ones. This is behavior-preserving for the
    rendered ACP prompt: an ``AgentContext`` with no skills/suffix/secrets
    renders the same "no context" content as ``None`` (see
    ``ACPAgent._render_suffix``). A ``custom`` server has no default command, so
    one must be supplied — the same invariant ``resolve_acp_command`` enforces
    at ``create_agent``, surfaced here so the resolved settings stay executable.
    """
    command = shlex.split(profile.acp_command) if profile.acp_command else []
    if profile.acp_server == "custom" and not command:
        raise ValueError(
            "acp_command is required when acp_server='custom' — there is no "
            "default launch command to fall back to"
        )
    agent_context = AgentContext(
        skills=filtered_skills, current_datetime=None, load_project_skills=True
    )
    payload = {
        "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
        "agent_kind": "acp",
        "acp_server": profile.acp_server,
        "acp_model": profile.acp_model,
        "acp_session_mode": profile.acp_session_mode,
        "acp_prompt_timeout": profile.acp_prompt_timeout,
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
) -> AgentSettingsConfig:
    """Resolve a profile's references into a validated ``AgentSettingsConfig``.

    ``mcp_config`` is the user's globally-configured MCP server map, already
    decrypted by the caller (the agent-server runs settings decryption
    before calling). ``available_skills`` is the server-discovered skill catalog
    that ``skill_refs`` filters by name (the agent-server caller passes the
    result of ``load_all_skills``); it feeds both variants — the OpenHands
    agent_context and the ACP prompt catalog. Like ``mcp_config`` it is a
    required argument so a caller cannot silently resolve a zero-skill agent by
    forgetting it, and a ``skill_refs`` entry absent from the catalog raises
    :class:`DanglingSkillRef` (mirroring the MCP contract) rather than being
    dropped. ``None`` means discovery was not run (e.g. an empty ``skill_refs``
    skips it): no catalog is consulted, so the resolved agent gets no skills
    from this filter (project skills, loaded separately by ``LocalConversation``,
    are unaffected). ``cipher`` decrypts the referenced LLM profile.

    Raises:
        ProfileNotFound: ``llm_profile_ref`` does not exist (OpenHands path).
        DanglingMcpServerRef: an ``mcp_server_refs`` entry is not in ``mcp_config``.
        DanglingSkillRef: a ``skill_refs`` entry is not in ``available_skills``.
    """
    filtered_mcp, _, dangling = _compute_mcp_filter(mcp_config, profile.mcp_server_refs)
    if dangling:
        raise DanglingMcpServerRef(dangling)

    filtered_skills, _, dangling_skills = _compute_skill_filter(
        available_skills, profile.skill_refs
    )
    if dangling_skills:
        raise DanglingSkillRef(dangling_skills)

    if isinstance(profile, OpenHandsAgentProfile):
        try:
            llm = llm_store.load(profile.llm_profile_ref, cipher=cipher)
        except FileNotFoundError as e:
            raise ProfileNotFound(
                f"LLM profile {profile.llm_profile_ref!r} not found"
            ) from e
        return _build_openhands_settings(profile, llm, filtered_mcp, filtered_skills)

    return _build_acp_settings(profile, filtered_mcp, filtered_skills)


def resolve_agent_profile_dry_run(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    *,
    llm_store: LLMProfileLoader,
    mcp_config: dict[str, MCPServer],
    available_skills: list[Skill] | None,
    cipher: Cipher | None = None,
) -> AgentProfileDiagnostics:
    """Compute :class:`AgentProfileDiagnostics` without raising or side effects.

    Mirrors :func:`resolve_agent_profile`'s composition but records dangling LLM /
    MCP / skill refs as diagnostics instead of raising, so the editor /
    ``/materialize`` (#3719) can show a faithful set/missing report with secrets
    redacted. ``available_skills=None`` (discovery skipped or failed) leaves the
    catalog unknown, so no skill ref is reported dangling.
    """
    filtered_mcp, resolved, dangling = _compute_mcp_filter(
        mcp_config, profile.mcp_server_refs
    )
    diagnostics = AgentProfileDiagnostics(
        agent_kind=profile.agent_kind,
        mcp_server_refs=profile.mcp_server_refs,
        resolved_mcp_config_keys=resolved,
        resolved_mcp_servers=resolved,
        dangling_mcp_server_refs=dangling,
    )
    if dangling:
        diagnostics.errors.append(
            "MCP server(s) not configured: " + ", ".join(dangling)
        )

    # Skill selection report (both variants). Computed once and reused by the
    # settings build below; a dangling ref adds an error (mirrors MCP), flipping
    # ``valid`` at the verdict line below.
    filtered_skills, resolved_skills, dangling_skills = _compute_skill_filter(
        available_skills, profile.skill_refs
    )
    diagnostics.skill_refs = profile.skill_refs
    diagnostics.resolved_skills = resolved_skills
    diagnostics.dangling_skill_refs = dangling_skills
    if dangling_skills:
        diagnostics.errors.append("Skill(s) not found: " + ", ".join(dangling_skills))

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
    else:
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
                    profile, llm, filtered_mcp, filtered_skills
                )
            else:
                settings = _build_acp_settings(profile, filtered_mcp, filtered_skills)
            # No expose context => secrets redacted (mcp env/headers, llm api_key).
            diagnostics.resolved_settings = settings.model_dump(mode="json")
        except Exception as e:
            diagnostics.valid = False
            diagnostics.errors.append(f"Failed to build agent settings: {e}")

    return diagnostics
