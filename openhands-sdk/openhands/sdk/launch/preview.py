from __future__ import annotations

from openhands.sdk.launch.errors import (
    AgentLaunchError,
    LaunchStoreError,
    UnresolvedProfileReferences,
)
from openhands.sdk.launch.finalize import (
    LaunchRuntime,
    _settings_for_runtime,
    finalize,
)
from openhands.sdk.launch.resolve import LaunchStores, _load_llm, _load_skills, resolve
from openhands.sdk.profiles.agent_profile import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.profiles.resolver import (
    AgentProfileDiagnostics,
    _acp_credential_channels,
    _api_key_set,
    _apply_disabled_skills,
    _compute_mcp_filter,
    _unusable_tools,
)
from openhands.sdk.settings.model import OpenHandsAgentSettings
from openhands.sdk.tool.defaults import BROWSER_TOOL_NAME


def preview_launch(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    stores: LaunchStores,
    runtime: LaunchRuntime,
    *,
    load_memory: bool = False,
    check_usable: bool = True,
) -> AgentProfileDiagnostics:
    """Report what launching ``profile`` into ``runtime`` would build, without raising.

    Runs :func:`resolve` and :func:`finalize` exactly as a launch does, so the
    report cannot disagree with one. ``check_usable=False`` skips the tool
    usability probes, for a runtime this process cannot probe.
    """
    _, resolved_keys, _ = _compute_mcp_filter(
        dict(stores.mcp_config), profile.mcp_server_refs
    )
    diagnostics = AgentProfileDiagnostics(
        agent_kind=profile.agent_kind,
        mcp_server_refs=profile.mcp_server_refs,
        resolved_mcp_config_keys=resolved_keys,
        secret_refs=profile.secret_refs,
    )
    if isinstance(profile, OpenHandsAgentProfile):
        diagnostics.llm_profile_ref = profile.llm_profile_ref
        diagnostics.disabled_skills = profile.disabled_skills
        diagnostics.meta_profile_ref = profile.meta_profile_ref
        diagnostics.unusable_tools = _unusable_tools(
            profile.tools,
            browser_available=runtime.browser_available,
            check_usable=check_usable,
        )
        failing = [n for n in diagnostics.unusable_tools if n != BROWSER_TOOL_NAME]
        if failing:
            diagnostics.errors.append(
                "Tool(s) this server cannot run: " + ", ".join(failing)
            )
    else:
        (
            diagnostics.acp_api_key_secret_name,
            diagnostics.acp_base_url_secret_name,
            diagnostics.acp_file_secret_names,
        ) = _acp_credential_channels(profile.acp_server)

    try:
        resolved = resolve(profile, stores)
        launched = finalize(resolved, runtime, load_memory=load_memory)
    except UnresolvedProfileReferences as exc:
        diagnostics.errors.extend(exc.problems)
        diagnostics.dangling_mcp_server_refs = exc.mcp_server_refs
        diagnostics.dangling_meta_profile_ref = exc.meta_profile_ref
        diagnostics.dangling_meta_profile_llm_refs = exc.meta_profile_llm_refs
        if isinstance(profile, OpenHandsAgentProfile) and exc.llm_profile_ref is None:
            diagnostics.llm_profile_resolved = True
            _report_llm_key_and_skills(diagnostics, profile, stores)
        return diagnostics
    except (AgentLaunchError, LaunchStoreError) as exc:
        diagnostics.errors.append(f"Failed to build agent settings: {exc}")
        return diagnostics

    settings = _settings_for_runtime(resolved.settings, runtime)
    if isinstance(settings, OpenHandsAgentSettings):
        diagnostics.llm_profile_resolved = True
        diagnostics.llm_api_key_set = _api_key_set(settings.llm)
    context = launched.agent.agent_context
    diagnostics.resolved_skills = [s.name for s in context.skills] if context else []
    if diagnostics.errors:
        return diagnostics
    diagnostics.resolved_settings = settings.model_copy(
        update={"agent_context": context}
    ).model_dump(mode="json")
    diagnostics.valid = True
    return diagnostics


def _report_llm_key_and_skills(
    diagnostics: AgentProfileDiagnostics,
    profile: OpenHandsAgentProfile,
    stores: LaunchStores,
) -> None:
    try:
        llm = _load_llm(stores, profile.llm_profile_ref)
        catalog = _load_skills(stores)
    except (AgentLaunchError, LaunchStoreError):
        return
    diagnostics.llm_api_key_set = llm is not None and _api_key_set(llm)
    diagnostics.resolved_skills = [
        skill.name for skill in _apply_disabled_skills(catalog, profile.disabled_skills)
    ]
