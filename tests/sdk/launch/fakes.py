"""In-memory stores for driving :func:`openhands.sdk.launch.resolve` in tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from openhands.sdk import LLM
from openhands.sdk.launch import LaunchStores
from openhands.sdk.llm.meta_profile_store import MetaProfile
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles.agent_profile import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.skills import Skill


class AgentProfiles:
    def __init__(self, *profiles: OpenHandsAgentProfile | ACPAgentProfile) -> None:
        self.profiles = {profile.name: profile for profile in profiles}

    def name_for_id(self, profile_id: str | UUID) -> str | None:
        return next(
            (
                name
                for name, profile in self.profiles.items()
                if str(profile.id) == str(profile_id)
            ),
            None,
        )

    def load(self, name: str) -> OpenHandsAgentProfile | ACPAgentProfile:
        try:
            return self.profiles[name]
        except KeyError:
            raise FileNotFoundError(name) from None


class LLMProfiles:
    def __init__(self, llms: Mapping[str, LLM]) -> None:
        self.llms = dict(llms)
        self.loads: list[str] = []

    def load(self, name: str, *, cipher: object = None) -> LLM:
        self.loads.append(name)
        try:
            return self.llms[name]
        except KeyError:
            raise FileNotFoundError(name) from None


class MetaProfiles:
    def __init__(self, metas: Mapping[str, MetaProfile]) -> None:
        self.metas = dict(metas)

    def load(self, name: str) -> MetaProfile:
        try:
            return self.metas[name]
        except KeyError:
            raise FileNotFoundError(name) from None


def llm(model: str = "gpt-4o", **kwargs: Any) -> LLM:
    return LLM(model=model, usage_id="agent", **kwargs)


def stores(
    *profiles: OpenHandsAgentProfile | ACPAgentProfile,
    llms: Mapping[str, LLM] | None = None,
    mcp: Mapping[str, MCPServer] | None = None,
    skills: Sequence[Skill] = (),
    metas: Mapping[str, MetaProfile] | None = None,
) -> LaunchStores:
    llm_profiles = LLMProfiles({"default": llm()} if llms is None else llms)
    return LaunchStores(
        llm_profiles=llm_profiles,
        llm_profile_names=lambda: list(llm_profiles.llms),
        mcp_config=dict(mcp or {}),
        skills=lambda: list(skills),
        agent_profiles=AgentProfiles(*profiles),
        meta_profiles=MetaProfiles(metas or {}),
    )
