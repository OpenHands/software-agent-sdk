from __future__ import annotations

from openhands.sdk.launch.errors import (
    AgentLaunchError,
    LaunchStoreError,
    UnresolvedProfileReferences,
)
from openhands.sdk.launch.finalize import LaunchRuntime, finalize
from openhands.sdk.launch.resolve import LaunchStores, resolve
from openhands.sdk.profiles.agent_profile import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.profiles.resolver import (
    AgentProfileDiagnostics,
    _acp_credential_channels,
    _api_key_set,
    _compute_mcp_filter,
)
from openhands.sdk.settings.model import OpenHandsAgentSettings


def preview_launch(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    stores: LaunchStores,
    runtime: LaunchRuntime,
    *,
    load_memory: bool = False,
) -> AgentProfileDiagnostics:
    """Report what launching ``profile`` into ``runtime`` would build, without raising.

    Runs :func:`resolve` and :func:`finalize` exactly as a launch does, so the
    report cannot disagree with one.
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
        diagnostics.llm_profile_resolved = (
            isinstance(profile, OpenHandsAgentProfile) and exc.llm_profile_ref is None
        )
        diagnostics.dangling_mcp_server_refs = exc.mcp_server_refs
        diagnostics.dangling_meta_profile_ref = exc.meta_profile_ref
        diagnostics.dangling_meta_profile_llm_refs = exc.meta_profile_llm_refs
        return diagnostics
    except (AgentLaunchError, LaunchStoreError) as exc:
        diagnostics.errors.append(f"Failed to build agent settings: {exc}")
        return diagnostics

    settings = resolved.settings
    if isinstance(settings, OpenHandsAgentSettings):
        diagnostics.llm_profile_resolved = True
        diagnostics.llm_api_key_set = _api_key_set(settings.llm)
    context = launched.agent.agent_context
    diagnostics.resolved_skills = [s.name for s in context.skills] if context else []
    diagnostics.resolved_settings = settings.model_dump(mode="json")
    diagnostics.resolved_tools = [tool.name for tool in launched.agent.tools]
    diagnostics.pending = list(launched.pending)
    diagnostics.valid = True
    return diagnostics
