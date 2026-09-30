from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.request import AgentLaunchAdditions
from openhands.sdk.launch.resolve import ResolvedLaunch
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.settings.model import OpenHandsAgentSettings
from openhands.sdk.tool.defaults import BROWSER_TOOL_NAME, default_tool_specs
from openhands.sdk.tool.spec import Tool


ACPSkillSourcing = Literal["native", "openhands_managed"]


class LaunchRuntime(BaseModel):
    """What the process that runs the agent can do."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    browser_available: bool | None = Field(
        default=False,
        description=(
            "Whether the browser tool set can run. null: not known until the "
            "runtime starts, so a preview leaves the decision to the launch."
        ),
    )
    acp_skill_sourcing: ACPSkillSourcing = Field(
        default="native",
        description=(
            "'native': an ACP CLI reads its own skills, so none are injected. "
            "'openhands_managed': inject the resolved skill catalog."
        ),
    )


_FINALIZE_TOKEN = object()


@dataclass(frozen=True)
class LaunchedAgent:
    """The agent a conversation starts with; only :func:`finalize` builds one."""

    agent: AgentBase
    profile: LaunchedAgentProfile | None
    pending: tuple[str, ...] = ()
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _FINALIZE_TOKEN:
            raise TypeError("LaunchedAgent is built only by finalize()")


LaunchSource = ResolvedLaunch | AgentBase


def finalize(
    source: LaunchSource,
    runtime: LaunchRuntime,
    *,
    additions: AgentLaunchAdditions | None = None,
    extra_suffixes: Sequence[str] = (),
    client_tools: Sequence[Tool] = (),
    load_memory: bool = False,
    managed_secrets: Collection[str] = (),
    launched_at: datetime | None = None,
) -> LaunchedAgent:
    """Build the launch agent for ``source`` in the process that will run it.

    Owns every launch-time field: the default tool set (with browser when the
    runtime can run it), ``current_datetime``, ``load_memory``, ACP skill
    sourcing, suffix additions, client tools and credentials the runtime
    manages itself. ``pending`` names runtime-dependent parts left undecided
    because ``runtime`` does not know them yet.
    """
    pending: list[str] = []
    if isinstance(source, ResolvedLaunch):
        settings = source.settings
        profile = source.profile
        if isinstance(settings, OpenHandsAgentSettings) and settings.tools is None:
            if runtime.browser_available is None:
                pending.append(BROWSER_TOOL_NAME)
            settings = settings.model_copy(
                update={
                    "tools": default_tool_specs(
                        enable_sub_agents=settings.enable_sub_agents,
                        enable_browser=bool(runtime.browser_available),
                    )
                }
            )
        agent: AgentBase = settings.create_agent()
    else:
        agent = source
        profile = None

    agent = _with_current_datetime(agent, launched_at)
    if load_memory or (additions is not None and additions.load_memory):
        agent = _with_load_memory(agent)
    agent = _apply_acp_skill_sourcing(agent, runtime.acp_skill_sourcing)
    appended = (additions.system_message_suffix_append or "") if additions else ""
    for suffix in (appended, *extra_suffixes):
        if suffix.strip():
            agent = _append_system_message_suffix(agent, suffix.strip())
    if managed_secrets:
        agent = _without_context_secrets(agent, managed_secrets)
    if client_tools:
        agent = _with_client_tools(agent, client_tools)
    return LaunchedAgent(
        agent=agent, profile=profile, pending=tuple(pending), _token=_FINALIZE_TOKEN
    )


def _with_current_datetime(agent: AgentBase, launched_at: datetime | None) -> AgentBase:
    context = agent.agent_context
    if context is None or context.current_datetime is None:
        return agent
    now = launched_at or _now_in_timezone_of(context.current_datetime)
    return agent.model_copy(
        update={"agent_context": context.model_copy(update={"current_datetime": now})}
    )


def _now_in_timezone_of(value: datetime | str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now().astimezone()
    if value.tzinfo is None:
        return datetime.now().astimezone()
    return datetime.now(value.tzinfo)


def _with_load_memory(agent: AgentBase) -> AgentBase:
    # A null agent_context means "no prompt context"; ACP relies on that to keep
    # a timestamp out of its prompt, so a synthesized context carries none.
    context = agent.agent_context or AgentContext(current_datetime=None)
    return agent.model_copy(
        update={"agent_context": context.model_copy(update={"load_memory": True})}
    )


def _apply_acp_skill_sourcing(
    agent: AgentBase, sourcing: ACPSkillSourcing
) -> AgentBase:
    if sourcing != "native" or not isinstance(agent, ACPAgent):
        return agent
    context = agent.agent_context
    if context is None or not (
        context.skills
        or context.load_user_skills
        or context.load_public_skills
        or context.registered_marketplaces
    ):
        return agent
    return agent.model_copy(
        update={
            "agent_context": context.model_copy(
                update={
                    "skills": [],
                    "load_user_skills": False,
                    "load_public_skills": False,
                    "registered_marketplaces": [],
                }
            )
        }
    )


def _append_system_message_suffix(agent: AgentBase, addition: str) -> AgentBase:
    context = agent.agent_context or AgentContext()
    existing = (context.system_message_suffix or "").strip()
    suffix = f"{existing}\n\n{addition}" if existing else addition
    return agent.model_copy(
        update={
            "agent_context": context.model_copy(
                update={"system_message_suffix": suffix}
            )
        }
    )


def _without_context_secrets(agent: AgentBase, names: Collection[str]) -> AgentBase:
    context = agent.agent_context
    if context is None or not context.secrets:
        return agent
    secrets = {k: v for k, v in context.secrets.items() if k not in names}
    if len(secrets) == len(context.secrets):
        return agent
    return agent.model_copy(
        update={"agent_context": context.model_copy(update={"secrets": secrets})}
    )


def _with_client_tools(agent: AgentBase, client_tools: Sequence[Tool]) -> AgentBase:
    existing = {tool.name for tool in agent.tools}
    added = [tool for tool in client_tools if tool.name not in existing]
    if not added:
        return agent
    return agent.model_copy(update={"tools": [*agent.tools, *added]})
