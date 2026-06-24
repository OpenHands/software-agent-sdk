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

import copy
import shlex
from typing import TYPE_CHECKING, Any

from fastmcp.mcp_config import MCPConfig
from pydantic import BaseModel, Field, SecretStr

from openhands.sdk.context.agent_context import AgentContext
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
from openhands.sdk.skills import Skill, merge_skills_by_name
from openhands.sdk.utils.pydantic_secrets import (
    REDACTED_SECRET_VALUE,
    decrypt_str_with_cipher_or_keep,
)


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore
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
    resolved_mcp_servers: list[str] = Field(default_factory=list)
    dangling_mcp_server_refs: list[str] = Field(default_factory=list)

    # Skill selection (both variants). ``dangling_skill_refs`` is a soft
    # signal — unlike MCP refs it does NOT flip ``valid`` (see
    # :func:`_compute_skill_filter`) — so the editor can flag a stale selection
    # without blocking materialize/launch.
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


def _server_names(mcp_config: MCPConfig | None) -> list[str]:
    return list(mcp_config.mcpServers.keys()) if mcp_config is not None else []


def _compute_mcp_filter(
    mcp_config: MCPConfig | None,
    refs: list[str] | None,
) -> tuple[MCPConfig | None, list[str], list[str]]:
    """Resolve ``mcp_server_refs`` against the user's ``mcp_config``.

    ``None`` → passthrough (all servers); a non-null list filters to the named
    keys. Returns ``(filtered_config, resolved_names, dangling_names)``; an empty
    filter result becomes ``None`` (the ``[] = none`` profile semantics).
    """
    if refs is None:
        return mcp_config, _server_names(mcp_config), []
    available = mcp_config.mcpServers if mcp_config is not None else {}
    resolved = [r for r in refs if r in available]
    dangling = [r for r in refs if r not in available]
    refs_set = set(refs)
    filtered = {k: v for k, v in available.items() if k in refs_set}
    filtered_config = MCPConfig(mcpServers=filtered) if filtered else None
    return filtered_config, resolved, dangling


def _compute_skill_filter(
    available_skills: list[Skill] | None,
    refs: list[str] | None,
) -> tuple[list[Skill], list[str], list[str]]:
    """Resolve ``skill_refs`` against the server-discovered skills.

    Mirrors :func:`_compute_mcp_filter`: ``None`` → passthrough (all discovered
    skills); a non-null list filters to the named skills, preserving the *ref*
    order so the editor's selection order is honored. Returns
    ``(filtered_skills, resolved_names, dangling_names)``.

    Unlike MCP refs (resolved against the user's stored config), a dangling
    skill ref is *not* an error: skills discovery is non-deterministic (public /
    org sources load over the network and can change between edits), so a stale
    ref silently drops out here and is surfaced as a soft diagnostic by the
    dry-run instead of failing conversation start.
    """
    available = list(available_skills or [])
    if refs is None:
        return available, [s.name for s in available], []
    by_name = {s.name: s for s in available}
    resolved = [r for r in refs if r in by_name]
    dangling = [r for r in refs if r not in by_name]
    filtered = [by_name[r] for r in resolved]
    return filtered, resolved, dangling


def _decrypt_skill_mcp_tools(skills: list[Skill], cipher: Cipher | None) -> list[Skill]:
    """Decrypt ``skills[].mcp_tools`` env/headers ciphertext using ``cipher``.

    ``Skill.mcp_tools`` has a masking serializer but no symmetric validator, so a
    profile loaded with a cipher returns these values as ciphertext; the resolver
    holds the cipher and decrypts them (see ``AgentProfileStore.load``). No-op
    without a cipher or for skills with no ``mcp_tools``.
    """
    if cipher is None:
        return skills
    out: list[Skill] = []
    for skill in skills:
        tools = skill.mcp_tools
        if not tools:
            out.append(skill)
            continue
        decrypted = _decrypt_mcp_dict(tools, cipher)
        out.append(skill.model_copy(update={"mcp_tools": decrypted}))
    return out


def _decrypt_mcp_dict(config: dict[str, Any], cipher: Cipher) -> dict[str, Any]:
    """Decrypt every ``mcpServers.*.env`` / ``.headers`` string value in place
    on a deep copy, leaving non-token (legacy plaintext) values unchanged."""
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
                k: decrypt_str_with_cipher_or_keep(cipher, v, description="MCP secret")
                for k, v in mapping.items()
            }
    return config


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
    mcp_config: MCPConfig | None,
    cipher: Cipher | None,
    available_skills: list[Skill] | None,
) -> AgentSettingsConfig:
    """Compose the resolved ``OpenHandsAgentSettings`` from a profile + LLM.

    The agent's skills are the union of the profile's explicitly embedded
    ``skills`` (decrypted) and the server-discovered skills selected by
    ``skill_refs``. Embedded skills are authoritative on a name conflict, so the
    catalog selector composes with hand-authored skills rather than shadowing
    them.
    """
    embedded = _decrypt_skill_mcp_tools(profile.skills, cipher)
    filtered_discovered, _, _ = _compute_skill_filter(
        available_skills, profile.skill_refs
    )
    skills = merge_skills_by_name(embedded, filtered_discovered)
    payload = {
        "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
        "agent_kind": "openhands",
        "agent": profile.agent,
        "llm": llm,
        "mcp_config": mcp_config,
        "agent_context": AgentContext(
            skills=skills,
            system_message_suffix=profile.system_message_suffix,
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
    mcp_config: MCPConfig | None,
    available_skills: list[Skill] | None,
) -> AgentSettingsConfig:
    """Compose the resolved ``ACPAgentSettings`` from a profile.

    The profile stores ``acp_command`` as a single shell string; the settings
    field is a token list, so a non-empty command is split with :func:`shlex.split`.
    No credential is set — provider creds ride ``state.secret_registry`` (#3720).

    ``skill_refs`` is honored here too: ACP agents receive the selected skills as
    prompt-only context (``agent_context.to_acp_prompt_context`` renders the same
    ``<available_skills>`` catalog the OpenHands agent gets). An ``agent_context``
    is built only when the filter yields skills — empty stays ``None`` so a
    skill-free ACP profile keeps the unchanged "no context" prompt. ``skills``
    discovered server-side carry no secret, so no cipher is needed.
    ``current_datetime=None`` matches ACP's own context convention (it does not
    inject a timestamp the way the OpenHands agent does).

    Enforces the launch invariant that ``resolve_acp_command`` checks at
    ``create_agent`` time: a ``custom`` server has no default command, so one
    must be supplied. Surfacing it here keeps the resolved settings actually
    executable (and the dry-run verdict honest) instead of deferring the failure
    to conversation start.
    """
    command = shlex.split(profile.acp_command) if profile.acp_command else []
    if profile.acp_server == "custom" and not command:
        raise ValueError(
            "acp_command is required when acp_server='custom' — there is no "
            "default launch command to fall back to"
        )
    filtered_skills, _, _ = _compute_skill_filter(available_skills, profile.skill_refs)
    agent_context = (
        AgentContext(skills=filtered_skills, current_datetime=None)
        if filtered_skills
        else None
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
    llm_store: LLMProfileStore,
    mcp_config: MCPConfig | None,
    available_skills: list[Skill] | None = None,
    cipher: Cipher | None = None,
) -> AgentSettingsConfig:
    """Resolve a profile's references into a validated ``AgentSettingsConfig``.

    ``mcp_config`` is the user's globally-configured MCP servers, already
    decrypted by the caller (the agent-server runs ``decrypt_mcp_config_secrets``
    before calling). ``available_skills`` is the server-discovered skill catalog
    that ``skill_refs`` filters by name (the agent-server caller passes the
    result of ``load_all_skills``); it feeds both variants — the OpenHands
    agent_context and the ACP prompt catalog. ``None`` means no discovery was
    run, so only an OpenHands profile's embedded ``skills`` reach the agent (an
    ACP profile gets none). ``cipher`` decrypts the referenced LLM profile and
    any ``skills[].mcp_tools`` ciphertext.

    Raises:
        ProfileNotFound: ``llm_profile_ref`` does not exist (OpenHands path).
        DanglingMcpServerRef: an ``mcp_server_refs`` entry is not in ``mcp_config``.
    """
    filtered_mcp, _, dangling = _compute_mcp_filter(mcp_config, profile.mcp_server_refs)
    if dangling:
        raise DanglingMcpServerRef(dangling)

    if isinstance(profile, OpenHandsAgentProfile):
        try:
            llm = llm_store.load(profile.llm_profile_ref, cipher=cipher)
        except FileNotFoundError as e:
            raise ProfileNotFound(
                f"LLM profile {profile.llm_profile_ref!r} not found"
            ) from e
        return _build_openhands_settings(
            profile, llm, filtered_mcp, cipher, available_skills
        )

    return _build_acp_settings(profile, filtered_mcp, available_skills)


def resolve_agent_profile_dry_run(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    *,
    llm_store: LLMProfileStore,
    mcp_config: MCPConfig | None,
    available_skills: list[Skill] | None = None,
    cipher: Cipher | None = None,
) -> AgentProfileDiagnostics:
    """Compute :class:`AgentProfileDiagnostics` without raising or side effects.

    Mirrors :func:`resolve_agent_profile`'s composition but records dangling LLM /
    MCP refs as diagnostics instead of raising, so the editor / ``/materialize``
    (#3719) can show a faithful set/missing report with secrets redacted.
    """
    filtered_mcp, resolved, dangling = _compute_mcp_filter(
        mcp_config, profile.mcp_server_refs
    )
    diagnostics = AgentProfileDiagnostics(
        agent_kind=profile.agent_kind,
        mcp_server_refs=profile.mcp_server_refs,
        resolved_mcp_servers=resolved,
        dangling_mcp_server_refs=dangling,
    )
    if dangling:
        diagnostics.errors.append(
            "MCP server(s) not configured: " + ", ".join(dangling)
        )

    # Skill selection report (both variants — ``skill_refs`` is on the base).
    # Dangling refs are recorded but do NOT add an error (see
    # _compute_skill_filter), so a stale selection is shown to the editor
    # without flipping ``valid``.
    _, resolved_skills, dangling_skills = _compute_skill_filter(
        available_skills, profile.skill_refs
    )
    diagnostics.skill_refs = profile.skill_refs
    diagnostics.resolved_skills = resolved_skills
    diagnostics.dangling_skill_refs = dangling_skills

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
                    profile, llm, filtered_mcp, cipher, available_skills
                )
            else:
                settings = _build_acp_settings(profile, filtered_mcp, available_skills)
            # No expose context => secrets redacted (mcp env/headers, llm api_key).
            diagnostics.resolved_settings = settings.model_dump(mode="json")
        except Exception as e:
            diagnostics.valid = False
            diagnostics.errors.append(f"Failed to build agent settings: {e}")

    return diagnostics
