"""Deployment inputs an agent profile is resolved against."""

from typing import Literal, NamedTuple

from openhands.agent_server.config import ACPSkillSourcing
from openhands.agent_server.skills_service import discover_profile_skills
from openhands.sdk.profiles import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.skills import Skill
from openhands.sdk.tool import BROWSER_TOOL_NAME, is_tool_usable


class ProfileLaunchInputs(NamedTuple):
    available_skills: list[Skill] | None
    skill_discovery_error: Exception | None
    browser_available: bool


def gather_profile_launch_inputs(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    acp_skill_sourcing: ACPSkillSourcing,
    conversation_runtime: Literal["local", "docker"] = "local",
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
        # This process cannot see a container's chromium; there the browser
        # tool set degrades to no tools if it is missing.
        browser_available=profile.agent_kind == "openhands"
        and (conversation_runtime == "docker" or is_tool_usable(BROWSER_TOOL_NAME)),
    )
