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
from collections.abc import Container
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, SecretStr, model_validator

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
from openhands.sdk.utils.deprecation import warn_deprecated
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

    # OpenHands LLM reference.
    llm_profile_ref: str | None = None
    llm_profile_resolved: bool = False
    llm_api_key_set: bool = False

    # MCP composition (both variants).
    mcp_server_refs: list[str] | None = None
    resolved_mcp_config_keys: list[str] = Field(default_factory=list)
    resolved_mcp_servers: list[str] = Field(
        default_factory=list,
        deprecated=True,
        description="Deprecated alias for resolved_mcp_config_keys.",
    )
    dangling_mcp_server_refs: list[str] = Field(default_factory=list)

    # Skill selection (OpenHands only). ``disabled_skills`` is a deny-list over
    # the discovered catalog, so — unlike ``mcp_server_refs`` — it can never
    # dangle: a disabled name absent from the catalog is a harmless no-op.
    # ``resolved_skills`` is what would actually reach the agent (catalog minus
    # disabled).
    disabled_skills: list[str] = Field(default_factory=list)
    resolved_skills: list[str] = Field(default_factory=list)

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

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_resolved_mcp_servers(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if value.get("resolved_mcp_servers") is None:
            return value
        warn_deprecated(
            "AgentProfileDiagnostics.resolved_mcp_servers",
            deprecated_in="1.31.0",
            removed_in="1.36.0",
            details="Use AgentProfileDiagnostics.resolved_mcp_config_keys instead.",
            stacklevel=3,
        )
        if value.get("resolved_mcp_config_keys") is None:
            return {
                **value,
                "resolved_mcp_config_keys": value["resolved_mcp_servers"],
            }
        return value

    @model_validator(mode="after")
    def _mirror_resolved_mcp_config_keys(self) -> AgentProfileDiagnostics:
        if (
            not self.__dict__.get("resolved_mcp_servers")
            and self.resolved_mcp_config_keys
        ):
            self.resolved_mcp_servers = list(self.resolved_mcp_config_keys)
        return self


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
        # Tri-state passthrough; create_agent materializes None.
        "tools": profile.tools,
        "agent_context": AgentContext(
            skills=filtered_skills,
            system_message_suffix=profile.system_message_suffix,
            load_project_skills=True,
            disabled_skills=profile.disabled_skills,
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
) -> AgentSettingsConfig:
    """Compose the resolved ``ACPAgentSettings`` from a profile.

    ``acp_command`` is stored as a shell string and split into the settings'
    token list. No credential is set — provider creds ride
    ``state.secret_registry``. ACP profiles carry no user/public skills (the ACP
    subprocess owns its context), so ``agent_context`` has no discovered skills;
    it is always built (never ``None``) only so ``load_project_skills=True``
    reaches ``LocalConversation``'s lazy load (``current_datetime=None`` matches
    ACP's no-timestamp convention). Caveat: an ACP CLI that already ingests repo
    files (e.g. AGENTS.md) may then see that content twice (#4019). A ``custom``
    server has no default command, so one must be supplied.
    """
    command = shlex.split(profile.acp_command) if profile.acp_command else []
    if profile.acp_server == "custom" and not command:
        raise ValueError(
            "acp_command is required when acp_server='custom' — there is no "
            "default launch command to fall back to"
        )
    agent_context = AgentContext(
        skills=[], current_datetime=None, load_project_skills=True
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
    (the agent-server caller passes the result of ``load_all_skills``); an
    OpenHands profile keeps all of it except the names in ``disabled_skills``.
    ``None`` means discovery was not run or failed: no catalog, so the resolved
    agent gets no user/public skills (project skills, loaded separately by
    ``LocalConversation``, are unaffected). Unlike the ``mcp_server_refs``
    allow-list, the ``disabled_skills`` deny-list can never dangle, so this
    never raises for skills. ``cipher`` decrypts the referenced LLM profile.

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
        return _build_openhands_settings(profile, llm, filtered_mcp, filtered_skills)

    return _build_acp_settings(profile, filtered_mcp)


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
    MCP refs as diagnostics instead of raising, so the editor / ``/materialize``
    (#3719) can show a faithful set/missing report with secrets redacted. Skills
    use a deny-list (``disabled_skills``) that can't dangle, so there is no skill
    error to report — ``resolved_skills`` is just the catalog minus the disabled
    names. ``available_skills=None`` (discovery skipped or failed) means no
    user/public skills resolve.
    """
    filtered_mcp, resolved, dangling = _compute_mcp_filter(
        mcp_config, profile.mcp_server_refs
    )
    diagnostics = AgentProfileDiagnostics(
        agent_kind=profile.agent_kind,
        mcp_server_refs=profile.mcp_server_refs,
        resolved_mcp_config_keys=resolved,
        dangling_mcp_server_refs=dangling,
    )
    if dangling:
        diagnostics.errors.append(
            "MCP server(s) not configured: " + ", ".join(dangling)
        )

    # Skill selection report (OpenHands only; ACP injects no user/public skills).
    # Deny-list semantics: the catalog minus disabled names, never dangling.
    if isinstance(profile, OpenHandsAgentProfile):
        filtered_skills = _apply_disabled_skills(
            available_skills, profile.disabled_skills
        )
        diagnostics.disabled_skills = profile.disabled_skills
    else:
        filtered_skills = []
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
                settings = _build_acp_settings(profile, filtered_mcp)
            # No expose context => secrets redacted (mcp env/headers, llm api_key).
            diagnostics.resolved_settings = settings.model_dump(mode="json")
        except Exception as e:
            diagnostics.valid = False
            diagnostics.errors.append(f"Failed to build agent settings: {e}")

    return diagnostics
