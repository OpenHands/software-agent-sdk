"""Tests for ``prepare_agent_launch`` — the one function that builds a launch.

Covers the runtime-dependent pieces (browser, streaming, ACP skill sourcing,
memory), per-launch additions, provenance, the structured dangling-reference
error, and the deprecated ``agent_settings`` launch, which becomes an inline
profile resolved by the same function (#5141).
"""

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import ACPAgent, Agent
from openhands.sdk.context import AgentContext
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.profiles import (
    ACPAgentProfile,
    AgentLaunchAdditions,
    AgentLaunchCatalog,
    AgentLaunchError,
    AgentLaunchRuntime,
    OpenHandsAgentProfile,
    UnresolvedProfileReferences,
    agent_settings_launch_source,
    prepare_agent_launch,
)
from openhands.sdk.secret import StaticSecret
from openhands.sdk.settings.model import (
    ACPAgentSettings,
    OpenHandsAgentSettings,
    validate_agent_settings,
)
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool


_LLM_SECRET = "sk-LLM-SECRET-SHOULD-NOT-LEAK"
_CRITIC_SECRET = "sk-CRITIC-SECRET"
_SUFFIX_APPEND = (
    "<RUNTIME_SERVICES>\n* Automation: http://localhost:18001\n</RUNTIME_SERVICES>"  # noqa: E501
)


@pytest.fixture
def llm_store(tmp_path: Path) -> LLMProfileStore:
    store = LLMProfileStore(base_dir=tmp_path / "llm")
    store.save(
        "default",
        LLM(model="gpt-4o", api_key=SecretStr(_LLM_SECRET), usage_id="x"),
        include_secrets=True,
    )
    store.save(
        "picked",
        LLM(model="claude-opus-5", api_key=SecretStr(_LLM_SECRET), usage_id="x"),
        include_secrets=True,
    )
    return store


@pytest.fixture
def mcp_config() -> dict[str, MCPServer]:
    return coerce_mcp_config(
        {"mcpServers": {"fetch": {"url": "https://fetch.test"}}},
    )


def _catalog(
    llm_store: LLMProfileStore,
    *,
    mcp_config: dict[str, MCPServer] | None = None,
    skills: list[Skill] | None = None,
) -> AgentLaunchCatalog:
    return AgentLaunchCatalog(
        llm_store=llm_store,
        mcp_config=mcp_config if mcp_config is not None else {},
        skills=skills,
    )


def _openhands_profile(**kwargs) -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(name="oh", llm_profile_ref="default", **kwargs)


# --------------------------------------------------------------------------- #
# Runtime-dependent pieces
# --------------------------------------------------------------------------- #


def test_default_tools_get_browser_only_when_the_runtime_has_it(
    llm_store: LLMProfileStore,
) -> None:
    profile = _openhands_profile()
    assert profile.tools is None

    with_browser = prepare_agent_launch(
        profile,
        catalog=_catalog(llm_store),
        runtime=AgentLaunchRuntime(browser_available=True),
    )
    without = prepare_agent_launch(
        profile,
        catalog=_catalog(llm_store),
        runtime=AgentLaunchRuntime(browser_available=False),
    )

    assert with_browser.agent is not None and without.agent is not None
    assert "browser_tool_set" in [t.name for t in with_browser.agent.tools]
    assert "browser_tool_set" not in [t.name for t in without.agent.tools]
    # The resolved view materializes the tool list, so a preview can show it.
    assert isinstance(with_browser.settings, OpenHandsAgentSettings)
    assert with_browser.settings.tools is not None


def test_explicit_tools_are_never_amended(llm_store: LLMProfileStore) -> None:
    profile = _openhands_profile(tools=[Tool(name="terminal")])
    plan = prepare_agent_launch(
        profile,
        catalog=_catalog(llm_store),
        runtime=AgentLaunchRuntime(browser_available=True),
    )
    assert plan.agent is not None
    assert [t.name for t in plan.agent.tools] == ["terminal"]


def test_streaming_is_forced_for_openhands_profiles(
    llm_store: LLMProfileStore,
) -> None:
    plan = prepare_agent_launch(
        _openhands_profile(),
        catalog=_catalog(llm_store),
        runtime=AgentLaunchRuntime(stream=True),
    )
    assert isinstance(plan.agent, Agent)
    assert plan.agent.llm.stream is True


def test_current_datetime_is_computed_at_launch(llm_store: LLMProfileStore) -> None:
    before = datetime.now().astimezone()
    plan = prepare_agent_launch(_openhands_profile(), catalog=_catalog(llm_store))
    assert isinstance(plan.settings, OpenHandsAgentSettings)
    stamped = plan.settings.agent_context.current_datetime
    assert isinstance(stamped, datetime)
    assert stamped >= before


def test_acp_launch_keeps_no_timestamp(llm_store: LLMProfileStore) -> None:
    plan = prepare_agent_launch(
        ACPAgentProfile(name="acp", acp_server="claude-code"),
        catalog=_catalog(llm_store),
    )
    assert isinstance(plan.settings, ACPAgentSettings)
    assert plan.settings.agent_context is not None
    assert plan.settings.agent_context.current_datetime is None


def test_acp_profile_gets_the_catalog_only_under_managed_sourcing(
    llm_store: LLMProfileStore,
) -> None:
    profile = ACPAgentProfile(name="acp", acp_server="claude-code")
    catalog_skills = [Skill(name="a", content="x")]

    native = prepare_agent_launch(
        profile,
        catalog=_catalog(llm_store, skills=catalog_skills),
        runtime=AgentLaunchRuntime(acp_skill_sourcing="native"),
    )
    managed = prepare_agent_launch(
        profile,
        catalog=_catalog(llm_store, skills=catalog_skills),
        runtime=AgentLaunchRuntime(acp_skill_sourcing="openhands_managed"),
    )

    assert native.settings is not None and native.settings.agent_context is not None
    assert managed.settings is not None and managed.settings.agent_context is not None
    assert native.settings.agent_context.skills == []
    assert [s.name for s in managed.settings.agent_context.skills] == ["a"]


def test_disabled_skills_deny_the_catalog(llm_store: LLMProfileStore) -> None:
    plan = prepare_agent_launch(
        _openhands_profile(disabled_skills=["b"]),
        catalog=_catalog(
            llm_store,
            skills=[Skill(name="a", content="x"), Skill(name="b", content="y")],
        ),
    )
    assert isinstance(plan.settings, OpenHandsAgentSettings)
    assert [s.name for s in plan.settings.agent_context.skills] == ["a"]
    # The deny-list rides the context so lazily-loaded project skills honor it.
    assert plan.settings.agent_context.disabled_skills == ["b"]


def test_global_memory_preference_is_stamped(llm_store: LLMProfileStore) -> None:
    plan = prepare_agent_launch(
        _openhands_profile(),
        catalog=_catalog(llm_store),
        runtime=AgentLaunchRuntime(load_memory=True),
    )
    assert isinstance(plan.settings, OpenHandsAgentSettings)
    assert plan.settings.agent_context.load_memory is True


# --------------------------------------------------------------------------- #
# Additions and provenance
# --------------------------------------------------------------------------- #


def test_additions_append_to_the_profile_suffix(llm_store: LLMProfileStore) -> None:
    plan = prepare_agent_launch(
        _openhands_profile(system_message_suffix="PROFILE_BASELINE"),
        catalog=_catalog(llm_store),
        additions=AgentLaunchAdditions(
            system_message_suffix_append=f"  {_SUFFIX_APPEND}  "
        ),
    )
    assert isinstance(plan.settings, OpenHandsAgentSettings)
    suffix = plan.settings.agent_context.system_message_suffix
    assert suffix == f"PROFILE_BASELINE\n\n{_SUFFIX_APPEND}"


def test_additions_cannot_widen_a_profiles_scope(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = _openhands_profile(
        tools=[Tool(name="terminal")],
        mcp_server_refs=[],
        secret_refs=["ALLOWED"],
        disabled_skills=["a"],
    )
    catalog = _catalog(
        llm_store, mcp_config=mcp_config, skills=[Skill(name="a", content="x")]
    )
    additions = AgentLaunchAdditions(
        system_message_suffix_append=_SUFFIX_APPEND, llm_profile_ref="picked"
    )

    plain = prepare_agent_launch(profile, catalog=catalog)
    added = prepare_agent_launch(profile, catalog=catalog, additions=additions)

    assert isinstance(plain.settings, OpenHandsAgentSettings)
    assert isinstance(added.settings, OpenHandsAgentSettings)
    assert added.settings.tools == plain.settings.tools
    assert added.settings.mcp_config == plain.settings.mcp_config == {}
    assert added.settings.agent_context.skills == plain.settings.agent_context.skills
    assert added.allowed_secrets == plain.allowed_secrets == frozenset({"ALLOWED"})


def test_llm_profile_override_swaps_the_llm_and_is_recorded(
    llm_store: LLMProfileStore,
) -> None:
    profile = _openhands_profile()
    plan = prepare_agent_launch(
        profile,
        catalog=_catalog(llm_store),
        additions=AgentLaunchAdditions(llm_profile_ref="picked"),
    )
    assert isinstance(plan.agent, Agent)
    assert plan.agent.llm.model == "claude-opus-5"
    assert plan.launched_profile is not None
    assert plan.launched_profile.llm_profile_ref == "picked"
    assert plan.launched_profile.agent_profile_id == profile.id


def test_llm_profile_override_rejects_non_openhands_sources(
    llm_store: LLMProfileStore,
) -> None:
    additions = AgentLaunchAdditions(llm_profile_ref="picked")
    with pytest.raises(AgentLaunchError, match="ACP"):
        prepare_agent_launch(
            ACPAgentProfile(name="acp", acp_server="claude-code"),
            catalog=_catalog(llm_store),
            additions=additions,
        )
    with pytest.raises(AgentLaunchError, match="Agent Profile"):
        prepare_agent_launch(
            Agent(llm=LLM(model="gpt-4o", usage_id="agent"), tools=[]),
            additions=additions,
        )


def test_provenance_marks_an_inline_draft(llm_store: LLMProfileStore) -> None:
    stored = prepare_agent_launch(
        _openhands_profile(secret_refs=[]),
        catalog=_catalog(llm_store),
        profile_origin="stored",
    )
    inline = prepare_agent_launch(
        _openhands_profile(secret_refs=[]),
        catalog=_catalog(llm_store),
        profile_origin="inline",
    )
    assert stored.launched_profile is not None
    assert inline.launched_profile is not None
    assert stored.launched_profile.inline is False
    assert inline.launched_profile.inline is True
    assert stored.allowed_secrets == frozenset()


def test_no_provenance_is_recorded_when_not_asked(llm_store: LLMProfileStore) -> None:
    plan = prepare_agent_launch(
        _openhands_profile(), catalog=_catalog(llm_store), profile_origin=None
    )
    assert plan.launched_profile is None


# --------------------------------------------------------------------------- #
# Raw agents
# --------------------------------------------------------------------------- #


def test_raw_agent_gets_memory_and_additions_but_keeps_its_own_llm() -> None:
    agent = Agent(
        llm=LLM(model="gpt-4o", usage_id="agent", stream=False),
        tools=[],
        agent_context=AgentContext(system_message_suffix="BASE"),
    )
    plan = prepare_agent_launch(
        agent,
        runtime=AgentLaunchRuntime(
            load_memory=True, stream=True, browser_available=True
        ),
        additions=AgentLaunchAdditions(system_message_suffix_append=_SUFFIX_APPEND),
    )
    assert isinstance(plan.agent, Agent)
    context = plan.agent.agent_context
    assert context is not None
    assert context.load_memory is True
    assert context.system_message_suffix == f"BASE\n\n{_SUFFIX_APPEND}"
    # A raw agent is the explicit low-level option: nothing else is imposed.
    assert plan.agent.llm.stream is False
    assert plan.agent.tools == []
    assert plan.settings is None
    assert plan.launched_profile is None


def test_raw_acp_agent_loses_managed_skills_under_native_sourcing() -> None:
    agent = ACPAgent(
        acp_command=["echo", "acp"],
        agent_context=AgentContext(
            skills=[Skill(name="a", content="x")], load_user_skills=True
        ),
    )
    plan = prepare_agent_launch(
        agent, runtime=AgentLaunchRuntime(acp_skill_sourcing="native")
    )
    assert isinstance(plan.agent, ACPAgent)
    assert plan.agent.agent_context is not None
    assert plan.agent.agent_context.skills == []
    assert plan.agent.agent_context.load_user_skills is False


def test_an_agent_profile_launch_needs_a_catalog() -> None:
    with pytest.raises(TypeError, match="catalog"):
        prepare_agent_launch(_openhands_profile())


# --------------------------------------------------------------------------- #
# Dangling references
# --------------------------------------------------------------------------- #


def test_every_dangling_reference_is_reported_at_once(
    llm_store: LLMProfileStore, mcp_config: dict[str, MCPServer]
) -> None:
    profile = OpenHandsAgentProfile(
        name="oh", llm_profile_ref="missing", mcp_server_refs=["fetch", "gone"]
    )
    with pytest.raises(UnresolvedProfileReferences) as exc_info:
        prepare_agent_launch(
            profile, catalog=_catalog(llm_store, mcp_config=mcp_config)
        )

    error = exc_info.value
    assert error.llm_profile_ref == "missing"
    assert error.mcp_server_refs == ["gone"]
    detail = error.to_detail()
    assert detail["code"] == "unresolved_profile_references"
    assert detail["dangling_llm_profile_ref"] == "missing"
    assert detail["dangling_mcp_server_refs"] == ["gone"]
    assert "missing" in detail["message"]


def test_an_overridden_llm_ref_that_dangles_names_the_override(
    llm_store: LLMProfileStore,
) -> None:
    with pytest.raises(UnresolvedProfileReferences) as exc_info:
        prepare_agent_launch(
            _openhands_profile(),
            catalog=_catalog(llm_store),
            additions=AgentLaunchAdditions(llm_profile_ref="gone"),
        )
    assert exc_info.value.llm_profile_ref == "gone"


# --------------------------------------------------------------------------- #
# Deprecated ``agent_settings`` launches
# --------------------------------------------------------------------------- #


def _agent_settings_payload() -> OpenHandsAgentSettings:
    settings = validate_agent_settings(
        {
            "agent_kind": "openhands",
            "llm": {
                "model": "gpt-4o",
                "usage_id": "agent",
                "api_key": _LLM_SECRET,
            },
            "tools": [{"name": "terminal"}],
            "tool_concurrency_limit": 3,
            "enable_switch_llm_tool": False,
            "mcp_config": {"fetch": {"url": "https://fetch.test"}},
            "verification": {
                "critic_enabled": True,
                "critic_api_key": _CRITIC_SECRET,
            },
            "agent_context": AgentContext(
                skills=[Skill(name="a", content="x"), Skill(name="b", content="y")],
                disabled_skills=["b"],
                system_message_suffix="CLIENT_SUFFIX",
                user_message_suffix="USER_SUFFIX",
                secrets={"CONTEXT_SECRET": StaticSecret(value=SecretStr("v"))},
                current_datetime="2020-01-01T00:00",
            ).model_dump(),
        }
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    return settings


def test_agent_settings_launch_resolves_what_the_client_sent() -> None:
    settings = _agent_settings_payload()
    profile, catalog = agent_settings_launch_source(settings)
    plan = prepare_agent_launch(
        profile,
        catalog=catalog,
        runtime=AgentLaunchRuntime(stream=True),
        profile_origin=None,
    )

    assert isinstance(plan.settings, OpenHandsAgentSettings)
    resolved = plan.settings
    assert [t.name for t in (resolved.tools or [])] == ["terminal"]
    assert resolved.tool_concurrency_limit == 3
    assert resolved.enable_switch_llm_tool is False
    assert list(resolved.mcp_config) == ["fetch"]
    assert isinstance(resolved.llm.api_key, SecretStr)
    assert resolved.llm.api_key.get_secret_value() == _LLM_SECRET
    assert resolved.llm.stream is True
    context = resolved.agent_context
    assert context.system_message_suffix == "CLIENT_SUFFIX"
    assert [s.name for s in context.skills] == ["a"]
    # Fields no Agent Profile models are carried through, not dropped.
    assert context.user_message_suffix == "USER_SUFFIX"
    assert context.secrets is not None and "CONTEXT_SECRET" in context.secrets
    critic_key = resolved.verification.critic_api_key
    assert isinstance(critic_key, SecretStr)
    assert critic_key.get_secret_value() == _CRITIC_SECRET
    # The stale saved timestamp is replaced at launch (#5141).
    assert isinstance(context.current_datetime, datetime)
    # No stored profile launched this conversation.
    assert plan.launched_profile is None
    assert plan.allowed_secrets is None


def test_agent_settings_acp_launch_keeps_non_profile_fields() -> None:
    settings = validate_agent_settings(
        {
            "agent_kind": "acp",
            "acp_server": "claude-code",
            "acp_startup_timeout": 120.0,
            "acp_isolate_data_dir": True,
            "agent_context": AgentContext(
                skills=[Skill(name="a", content="x")], load_user_skills=True
            ).model_dump(),
        }
    )
    profile, catalog = agent_settings_launch_source(settings)
    plan = prepare_agent_launch(
        profile,
        catalog=catalog,
        runtime=AgentLaunchRuntime(acp_skill_sourcing="native"),
        profile_origin=None,
    )

    assert isinstance(plan.settings, ACPAgentSettings)
    assert plan.settings.acp_startup_timeout == 120.0
    assert plan.settings.acp_isolate_data_dir is True
    context = plan.settings.agent_context
    assert context is not None
    assert context.skills == []
    assert context.load_user_skills is False
