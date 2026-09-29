"""Deployment inputs an agent profile is resolved against."""

from typing import NamedTuple

from openhands.agent_server.config import ACPSkillSourcing, Config
from openhands.agent_server.skills_service import discover_profile_skills
from openhands.sdk.profiles import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.settings import AgentSettingsConfig, OpenHandsAgentSettings
from openhands.sdk.skills import Skill
from openhands.sdk.tool import BROWSER_TOOL_NAME, is_tool_usable, launch_tool_specs


class ProfileLaunchInputs(NamedTuple):
    available_skills: list[Skill] | None
    skill_discovery_error: Exception | None
    browser_available: bool


def can_probe_tools(config: Config) -> bool:
    """Whether conversations run in this process, so tool usability can be probed."""
    return config.conversation_runtime != "docker"


def configured_browser_available(config: Config) -> bool | None:
    """Browser availability fixed by ``config``; ``None`` means probe this process."""
    if not config.enable_browser:
        return False
    if config.conversation_runtime == "docker":
        return config.conversation_image_has_browser
    return None


def _browser_available(configured: bool | None) -> bool:
    return configured if configured is not None else is_tool_usable(BROWSER_TOOL_NAME)


def resolve_settings_tools(
    settings: AgentSettingsConfig, *, browser_available: bool | None = None
) -> AgentSettingsConfig:
    """Resolve an OpenHands agent's ``tools`` for the runtime it launches on."""
    if not isinstance(settings, OpenHandsAgentSettings):
        return settings
    tools = launch_tool_specs(
        settings.tools, browser_available=_browser_available(browser_available)
    )
    return settings.model_copy(update={"tools": tools})


def gather_profile_launch_inputs(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    acp_skill_sourcing: ACPSkillSourcing,
    browser_available: bool | None = None,
) -> ProfileLaunchInputs:
    """Discover the skill catalog and the browser availability for ``profile``."""
    available_skills = None
    discovery_error = None
    if profile.agent_kind == "openhands" or acp_skill_sourcing == "openhands_managed":
        try:
            available_skills = discover_profile_skills()
        except Exception as exc:
            discovery_error = exc
    return ProfileLaunchInputs(
        available_skills=available_skills,
        skill_discovery_error=discovery_error,
        browser_available=profile.agent_kind == "openhands"
        and _browser_available(browser_available),
    )
