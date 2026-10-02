from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation.request import AgentLaunchAdditions
from openhands.sdk.launch import (
    AgentLaunchError,
    LaunchedAgent,
    LaunchRuntime,
    ResolvedLaunch,
    finalize,
)
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.secret import StaticSecret
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool


LAUNCHED_AT = datetime(2026, 9, 30, 12, 10, tzinfo=UTC)


def _settings(**kwargs) -> ResolvedLaunch:
    return ResolvedLaunch(
        settings=OpenHandsAgentSettings(
            llm=LLM(model="gpt-4o", usage_id="agent"), **kwargs
        )
    )


def _agent(context: AgentContext | None = None) -> Agent:
    return Agent(
        llm=LLM(model="gpt-4o", usage_id="agent"), tools=[], agent_context=context
    )


def _tool_names(launched: LaunchedAgent) -> list[str]:
    return [tool.name for tool in launched.agent.tools]


def test_only_finalize_builds_a_launched_agent():
    with pytest.raises(TypeError, match="finalize"):
        LaunchedAgent(agent=_agent(), profile=None)


@pytest.mark.parametrize(
    ("browser", "expected"),
    [
        (True, ["terminal", "file_editor", "task_tracker", "browser_tool_set"]),
        (False, ["terminal", "file_editor", "task_tracker"]),
    ],
)
def test_unset_tools_get_the_default_set_with_browser_when_usable(browser, expected):
    launched = finalize(_settings(), LaunchRuntime(browser_available=browser))

    assert _tool_names(launched) == expected
    assert isinstance(launched.agent, Agent)
    assert "SwitchLLMTool" in launched.agent.include_default_tools


@pytest.mark.parametrize(
    ("browser", "expected"),
    [(True, ["terminal", "browser_tool_set"]), (False, ["terminal"])],
)
def test_an_explicit_tool_list_keeps_the_browser_only_when_usable(browser, expected):
    launched = finalize(
        _settings(tools=[Tool(name="terminal"), Tool(name="browser_tool_set")]),
        LaunchRuntime(browser_available=browser),
    )

    assert _tool_names(launched) == expected


def test_an_acp_agent_never_gets_the_browser():
    source = ResolvedLaunch(settings=ACPAgentSettings(acp_command=["echo"]))

    launched = finalize(source, LaunchRuntime(browser_available=True))

    assert isinstance(launched.agent, ACPAgent)
    assert launched.agent.tools == []


def test_provenance_passes_through_and_a_raw_agent_has_none():
    profile = LaunchedAgentProfile(agent_profile_id=uuid4(), revision=2)
    resolved = ResolvedLaunch(settings=_settings().settings, profile=profile)

    assert finalize(resolved, LaunchRuntime()).profile == profile
    assert finalize(_agent(), LaunchRuntime()).profile is None


@pytest.mark.parametrize(
    "stale",
    ["2026-09-30T11:54:29+02:00", datetime(2026, 9, 30, 11, 54, tzinfo=UTC)],
)
def test_a_stale_timestamp_is_replaced_at_launch(stale):
    source = _settings(agent_context=AgentContext(current_datetime=stale))

    launched = finalize(source, LaunchRuntime(), launched_at=LAUNCHED_AT)

    context = launched.agent.agent_context
    assert context is not None
    assert context.current_datetime == LAUNCHED_AT


def test_a_raw_agent_timestamp_keeps_its_timezone():
    tz = timezone(timedelta(hours=-7))
    agent = _agent(AgentContext(current_datetime=datetime(2020, 1, 1, tzinfo=tz)))

    context = finalize(agent, LaunchRuntime()).agent.agent_context

    assert context is not None
    assert isinstance(context.current_datetime, datetime)
    assert context.current_datetime.utcoffset() == timedelta(hours=-7)
    assert context.current_datetime.year >= 2026


@pytest.mark.parametrize(
    "context", [None, AgentContext(current_datetime=None)], ids=["none", "suppressed"]
)
def test_a_suppressed_timestamp_stays_suppressed(context):
    launched = finalize(_agent(context), LaunchRuntime(), launched_at=LAUNCHED_AT)

    after = launched.agent.agent_context
    assert after is None or after.current_datetime is None


def test_a_pre_formatted_timestamp_is_kept():
    stamp = "Friday, 15 March 2024 (simulated)"

    launched = finalize(
        _agent(AgentContext(current_datetime=stamp)),
        LaunchRuntime(),
        launched_at=LAUNCHED_AT,
    )

    context = launched.agent.agent_context
    assert context is not None
    assert context.current_datetime == stamp


def test_memory_is_loaded_when_the_preference_is_on():
    launched = finalize(_agent(), LaunchRuntime(), load_memory=True)

    context = launched.agent.agent_context
    assert context is not None
    assert context.load_memory is True
    # A context synthesized for memory carries no timestamp.
    assert context.current_datetime is None


def test_suffix_additions_are_appended_in_order():
    agent = _agent(AgentContext(system_message_suffix="PROFILE"))

    launched = finalize(
        agent,
        LaunchRuntime(),
        additions=AgentLaunchAdditions(system_message_suffix_append="  RUNTIME  "),
        extra_suffixes=["WORKTREE", "   "],
    )

    context = launched.agent.agent_context
    assert context is not None
    assert context.system_message_suffix == "PROFILE\n\nRUNTIME\n\nWORKTREE"


def test_a_context_synthesized_for_a_suffix_carries_no_timestamp():
    launched = finalize(_agent(), LaunchRuntime(), extra_suffixes=["WORKTREE"])

    context = launched.agent.agent_context
    assert context is not None
    assert context.system_message_suffix == "WORKTREE"
    assert context.current_datetime is None


def test_settings_that_cannot_build_an_agent_are_a_launch_error(monkeypatch):
    def fail(self):
        raise ValueError("OpenAI subscription login is required")

    monkeypatch.setattr(OpenHandsAgentSettings, "create_agent", fail)

    with pytest.raises(AgentLaunchError, match="subscription login"):
        finalize(_settings(), LaunchRuntime())


def test_native_sourcing_strips_managed_skills_from_an_acp_agent():
    skill = Skill(name="managed", content="x")
    source = ResolvedLaunch(
        settings=ACPAgentSettings(
            acp_command=["echo"],
            agent_context=AgentContext(skills=[skill], current_datetime=None),
        )
    )

    native = finalize(source, LaunchRuntime(acp_skill_sourcing="native"))
    managed = finalize(source, LaunchRuntime(acp_skill_sourcing="openhands_managed"))

    assert native.agent.agent_context is not None
    assert native.agent.agent_context.skills == []
    assert managed.agent.agent_context is not None
    assert [s.name for s in managed.agent.agent_context.skills] == ["managed"]


def test_managed_secrets_are_dropped_from_the_agent_context():
    agent = _agent(
        AgentContext(
            secrets={
                "CODEX_AUTH_JSON": StaticSecret(value=SecretStr("{}")),
                "GITHUB_TOKEN": StaticSecret(value=SecretStr("gh")),
            }
        )
    )

    launched = finalize(agent, LaunchRuntime(), managed_secrets=["CODEX_AUTH_JSON"])

    context = launched.agent.agent_context
    assert context is not None and context.secrets is not None
    assert list(context.secrets) == ["GITHUB_TOKEN"]


def test_client_tools_are_added_once():
    agent = _agent().model_copy(update={"tools": [Tool(name="canvas_ui")]})

    launched = finalize(
        agent,
        LaunchRuntime(),
        client_tools=[Tool(name="canvas_ui"), Tool(name="launch_child")],
    )

    assert _tool_names(launched) == ["canvas_ui", "launch_child"]
