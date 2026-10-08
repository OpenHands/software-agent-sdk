from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.agent.acp_agent import (
    ACPAgent,
    ACPSkillSourcing,
    _managed_catalog_is_injected,
    _strip_managed_skills,
)
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.request import AgentLaunchAdditions
from openhands.sdk.launch.errors import AgentLaunchError
from openhands.sdk.launch.resolve import ResolvedLaunch
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings
from openhands.sdk.tool.defaults import launch_tool_specs
from openhands.sdk.tool.spec import Tool


class LaunchRuntime(BaseModel):
    """What the process that runs the agent can do."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    browser_available: bool = Field(
        default=False, description="Whether the browser tool set can run."
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

    Owns every launch-time field: the tool set (with browser only when the
    runtime can run it), ``current_datetime``, ``load_memory``, ACP skill
    sourcing, suffix additions, client tools and credentials the runtime
    manages itself.
    """
    if isinstance(source, ResolvedLaunch):
        settings = _settings_for_runtime(source.settings, runtime)
        try:
            agent: AgentBase = settings.create_agent()
        except (TypeError, ValueError) as exc:
            raise AgentLaunchError(str(exc)) from exc
        profile = source.profile
    else:
        agent = source
        profile = None

    agent = _with_current_datetime(agent, launched_at)
    if load_memory:
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
    return LaunchedAgent(agent=agent, profile=profile, _token=_FINALIZE_TOKEN)


def _settings_for_runtime(
    settings: OpenHandsAgentSettings | ACPAgentSettings, runtime: LaunchRuntime
) -> OpenHandsAgentSettings | ACPAgentSettings:
    if not isinstance(settings, OpenHandsAgentSettings):
        return settings
    tools = launch_tool_specs(
        settings.tools, browser_available=runtime.browser_available
    )
    return settings.model_copy(update={"tools": tools})


def _with_current_datetime(agent: AgentBase, launched_at: datetime | None) -> AgentBase:
    context = agent.agent_context
    if context is None or context.current_datetime is None:
        return agent
    now = _now_in_timezone_of(context.current_datetime)
    if now is None:
        return agent
    return agent.model_copy(
        update={
            "agent_context": context.model_copy(
                update={"current_datetime": launched_at or now}
            )
        }
    )


def _now_in_timezone_of(value: datetime | str) -> datetime | None:
    """Now, in ``value``'s timezone; None when ``value`` is pre-formatted text."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        return datetime.now().astimezone()
    return datetime.now(value.tzinfo)


def _context_of(agent: AgentBase) -> AgentContext:
    # A null agent_context means "no prompt context"; ACP relies on that to keep
    # a timestamp out of its prompt, so a synthesized context carries none.
    return agent.agent_context or AgentContext(current_datetime=None)


def _with_load_memory(agent: AgentBase) -> AgentBase:
    context = _context_of(agent)
    return agent.model_copy(
        update={"agent_context": context.model_copy(update={"load_memory": True})}
    )


def _apply_acp_skill_sourcing(
    agent: AgentBase, sourcing: ACPSkillSourcing
) -> AgentBase:
    """Record the runtime's skill sourcing on an ACP agent.

    The agent renders the ACP prompt itself, so the sourcing has to live on the
    agent — ``finalize`` is the only code that knows the runtime. Recorded even
    when nothing needs stripping: a managed deployment whose catalog arrives
    later (lazy flags, plugin skills) must not be rendered under the unknown
    default (``None``) a directly-built agent carries.
    """
    if not isinstance(agent, ACPAgent):
        return agent
    context = agent.agent_context
    stripped = context
    if (
        context is not None
        and sourcing == "native"
        and _managed_catalog_is_injected(context)
    ):
        stripped = _strip_managed_skills(context)
    if stripped is context and agent.acp_skill_sourcing == sourcing:
        return agent
    return agent.model_copy(
        update={"acp_skill_sourcing": sourcing, "agent_context": stripped}
    )


def _append_system_message_suffix(agent: AgentBase, addition: str) -> AgentBase:
    context = _context_of(agent)
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
