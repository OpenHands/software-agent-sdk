"""Turn a conversation start request into the agent it launches with.

Every launch path (local conversations, the Docker runtime, and the
materialize preview) resolves its agent here, through
:func:`~openhands.sdk.profiles.prepare_agent_launch`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError

from openhands.agent_server.persistence.models import PersistedSettings
from openhands.agent_server.persistence.store import (
    get_agent_profile_store,
    get_llm_profile_store,
)
from openhands.agent_server.skills_service import discover_profile_skills
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.profiles import (
    ACPAgentProfile,
    AgentLaunchCatalog,
    AgentLaunchError,
    AgentLaunchPlan,
    AgentLaunchRuntime,
    OpenHandsAgentProfile,
    ProfileNotFound,
    agent_settings_launch_source,
    prepare_agent_launch,
)
from openhands.sdk.profiles.resolver import ACPSkillSourcing, ProfileOrigin
from openhands.sdk.settings.model import validate_agent_settings
from openhands.sdk.tool import BROWSER_TOOL_NAME, is_tool_usable
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.deprecation import warn_deprecated


@dataclass(frozen=True, kw_only=True)
class LaunchSource:
    """A request's agent source, loaded and ready to resolve."""

    source: OpenHandsAgentProfile | ACPAgentProfile | AgentBase
    catalog: AgentLaunchCatalog | None
    profile_origin: ProfileOrigin | None


def _error_text(exc: Exception) -> str:
    # A ValidationError's str() echoes the rejected input.
    if isinstance(exc, ValidationError):
        return "; ".join(err["msg"] for err in exc.errors())
    return str(exc)


def launch_runtime(
    settings: PersistedSettings,
    *,
    acp_skill_sourcing: ACPSkillSourcing,
    browser_available: bool | None = None,
) -> AgentLaunchRuntime:
    """This server's launch runtime; ``browser_available=None`` probes this process."""
    if browser_available is None:
        browser_available = is_tool_usable(BROWSER_TOOL_NAME)
    context = settings.agent_settings.agent_context
    return AgentLaunchRuntime(
        browser_available=browser_available,
        acp_skill_sourcing=acp_skill_sourcing,
        stream=True,
        load_memory=bool(context and context.load_memory),
    )


def load_stored_profile(profile_id: UUID) -> OpenHandsAgentProfile | ACPAgentProfile:
    store = get_agent_profile_store()
    name = store.name_for_id(profile_id)
    if name is None:
        raise ProfileNotFound(f"Agent profile with id '{profile_id}' not found")
    try:
        return store.load(name)
    except FileNotFoundError as exc:
        raise ProfileNotFound(
            f"Agent profile '{name}' (id={profile_id}) not found"
        ) from exc
    except ValueError as exc:
        raise AgentLaunchError(f"Failed to load agent profile '{name}': {exc}") from exc


def profile_catalog(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    *,
    cipher: Cipher | None,
    settings: PersistedSettings,
    runtime: AgentLaunchRuntime,
) -> AgentLaunchCatalog:
    """Resolve ``profile`` against this server's stores and skill catalog."""
    skills = None
    if runtime.uses_skill_catalog(profile.agent_kind):
        try:
            skills = discover_profile_skills()
        except Exception as exc:
            raise AgentLaunchError(
                f"Skill discovery failed for profile '{profile.name}': {exc}"
            ) from exc
    return AgentLaunchCatalog(
        llm_store=get_llm_profile_store(),
        mcp_config=settings.agent_settings.mcp_config,
        skills=skills,
        cipher=cipher,
    )


def load_launch_source(
    request: StartConversationRequest,
    *,
    cipher: Cipher | None,
    settings: PersistedSettings,
    runtime: AgentLaunchRuntime,
) -> LaunchSource:
    """Load the request's agent source. Blocking: call from a worker thread."""
    agent = cast(AgentBase | None, request.agent)
    if request.agent_profile_id is not None:
        profile = load_stored_profile(request.agent_profile_id)
        origin: ProfileOrigin = "stored"
    elif request.agent_profile is not None:
        profile = request.agent_profile
        origin = "inline"
    elif agent is not None:
        return LaunchSource(source=agent, catalog=None, profile_origin=None)
    else:
        warn_deprecated(
            "StartConversationRequest.agent_settings",
            deprecated_in="1.50.0",
            removed_in="1.55.0",
            details="Use agent_profile_id or agent_profile instead.",
        )
        context = {"cipher": cipher} if request.secrets_encrypted else None
        try:
            agent_settings = validate_agent_settings(
                request.agent_settings, context=context
            )
        except (TypeError, ValueError) as exc:
            raise AgentLaunchError(
                f"Invalid agent_settings: {_error_text(exc)}"
            ) from exc
        profile, catalog = agent_settings_launch_source(agent_settings)
        return LaunchSource(source=profile, catalog=catalog, profile_origin=None)

    return LaunchSource(
        source=profile,
        catalog=profile_catalog(
            profile, cipher=cipher, settings=settings, runtime=runtime
        ),
        profile_origin=origin,
    )


def apply_launch(
    request: StartConversationRequest,
    source: LaunchSource,
    runtime: AgentLaunchRuntime,
    *,
    build_agent: bool = True,
) -> tuple[StartConversationRequest, AgentLaunchPlan]:
    """Resolve ``source`` and fold the result into a request carrying only ``agent``.

    The profile's secret scope is enforced on ``request.secrets`` here, so a
    caller cannot widen it by sending more secrets than the profile allows.
    """
    try:
        plan = prepare_agent_launch(
            source.source,
            catalog=source.catalog,
            runtime=runtime,
            additions=request.agent_launch_additions,
            profile_origin=source.profile_origin,
            build_agent=build_agent,
        )
    except AgentLaunchError:
        raise
    except (TypeError, ValueError) as exc:
        raise AgentLaunchError(f"Agent failed to resolve: {_error_text(exc)}") from exc
    secrets = request.secrets
    if plan.allowed_secrets is not None:
        secrets = {
            name: value
            for name, value in secrets.items()
            if name in plan.allowed_secrets
        }
    updates: dict[str, Any] = {
        "agent_profile_id": None,
        "agent_profile": None,
        "agent_settings": None,
        "agent_launch_additions": None,
        "secrets": secrets,
    }
    if plan.agent is not None:
        updates["agent"] = plan.agent
    return request.model_copy(update=updates), plan


def prepare_launch_request(
    request: StartConversationRequest,
    *,
    cipher: Cipher | None,
    settings: PersistedSettings,
    runtime: AgentLaunchRuntime,
) -> tuple[StartConversationRequest, AgentLaunchPlan]:
    """Load and resolve a request's agent. Blocking: call from a worker thread."""
    source = load_launch_source(
        request, cipher=cipher, settings=settings, runtime=runtime
    )
    return apply_launch(request, source, runtime)
