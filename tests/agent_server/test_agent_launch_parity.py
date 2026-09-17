"""Every product launch path builds the same agent (#5141).

The profile's *name* must not matter, the deprecated ``agent_settings`` payload
must take the same pipeline, and the ``materialize`` preview must agree with a
real launch field by field.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.agent_launch import launch_runtime, prepare_launch_request
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
    get_settings_store,
)
from openhands.sdk.agent import Agent
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.llm import LLM
from openhands.sdk.profiles import (
    AgentLaunchAdditions,
    OpenHandsAgentProfile,
    ProfileNotFound,
    UnresolvedProfileReferences,
)
from openhands.sdk.secret import StaticSecret
from openhands.sdk.settings.model import (
    LLMSummarizingCondenserSettings,
    OpenHandsAgentSettings,
    validate_agent_settings,
)
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool
from openhands.sdk.workspace import LocalWorkspace


_CATALOG = [Skill(name="kept", content="x"), Skill(name="denied", content="y")]


@pytest.fixture(autouse=True)
def stub_skill_discovery(monkeypatch):
    """Pin the catalog: real discovery clones the public skills repository."""
    monkeypatch.setattr(
        "openhands.agent_server.agent_launch.discover_profile_skills",
        lambda: list(_CATALOG),
    )
    monkeypatch.setattr(
        "openhands.agent_server.agent_profiles_router.discover_profile_skills",
        lambda: list(_CATALOG),
    )


@pytest.fixture
def config() -> Config:
    return Config(static_files_path=None, session_api_keys=[], secret_key=None)


@pytest.fixture
def settings(config: Config) -> PersistedSettings:
    stored = PersistedSettings(
        agent_settings=validate_agent_settings(
            {
                "agent_kind": "openhands",
                "llm": {"model": "gpt-4o", "usage_id": "agent"},
                "mcp_config": {"fetch": {"url": "https://fetch.test"}},
            }
        )
    )
    get_settings_store(config).save(stored)
    return stored


@pytest.fixture
def llm_profile() -> str:
    get_llm_profile_store().save(
        "primary",
        LLM(model="gpt-4o", api_key=SecretStr("sk-primary"), usage_id="agent"),
        include_secrets=True,
    )
    get_llm_profile_store().save(
        "alternate",
        LLM(model="claude-opus-5", api_key=SecretStr("sk-alt"), usage_id="agent"),
        include_secrets=True,
    )
    return "primary"


def _profile(name: str, llm_profile_ref: str) -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(
        name=name,
        llm_profile_ref=llm_profile_ref,
        tools=[Tool(name="terminal")],
        system_message_suffix="PROFILE_SUFFIX",
        disabled_skills=["denied"],
        enable_switch_llm_tool=False,
        tool_concurrency_limit=3,
        mcp_server_refs=[],
    )


def _launch(
    profile_id: UUID | None,
    settings: PersistedSettings,
    *,
    agent_settings: dict[str, Any] | None = None,
    additions: AgentLaunchAdditions | None = None,
) -> tuple[StartConversationRequest, Any]:
    request = StartConversationRequest(
        agent_profile_id=profile_id,
        agent_settings=agent_settings,
        workspace=LocalWorkspace(working_dir="/tmp/parity"),
        agent_launch_additions=additions,
    )
    return prepare_launch_request(
        request,
        cipher=None,
        settings=settings,
        runtime=launch_runtime(
            settings, acp_skill_sourcing="native", browser_available=True
        ),
    )


def _agent_fingerprint(agent: Agent) -> dict[str, Any]:
    """The agent fields a profile owns, with launch-time values normalized."""
    context = agent.agent_context
    assert context is not None
    return {
        "llm": agent.llm.model_dump(mode="json", exclude={"stream"}),
        "stream": agent.llm.stream,
        "tools": [t.name for t in agent.tools],
        "mcp_keys": sorted(agent.mcp_config),
        "skills": sorted(s.name for s in context.skills),
        "suffix": context.system_message_suffix,
        "disabled_skills": context.disabled_skills,
        "load_project_skills": context.load_project_skills,
        "load_memory": context.load_memory,
        "condenser": agent.condenser.model_dump(mode="json")
        if agent.condenser
        else None,
        "critic": agent.critic.model_dump(mode="json") if agent.critic else None,
        "concurrency": agent.tool_concurrency_limit,
        "switch_llm": "switch_llm" in [t.name for t in agent.tools]
        or "SwitchLLMTool" in agent.include_default_tools,
    }


def test_a_profile_named_default_launches_like_any_other(
    settings: PersistedSettings, llm_profile: str
) -> None:
    store = get_agent_profile_store()
    store.save(_profile("default", llm_profile))
    store.save(_profile("default-copy", llm_profile))

    ids = {s["name"]: UUID(str(s["id"])) for s in store.list_summaries()}
    _, default_plan = _launch(ids["default"], settings)
    _, copy_plan = _launch(ids["default-copy"], settings)

    assert isinstance(default_plan.agent, Agent)
    assert isinstance(copy_plan.agent, Agent)
    assert _agent_fingerprint(default_plan.agent) == _agent_fingerprint(copy_plan.agent)


def test_agent_settings_launch_takes_the_profile_pipeline(
    settings: PersistedSettings, llm_profile: str
) -> None:
    """The deprecated payload becomes an inline profile, so the launch-time
    fields (a fresh timestamp, forced streaming) come out the same."""
    payload = {
        "agent_kind": "openhands",
        "llm": {"model": "gpt-4o", "usage_id": "agent", "api_key": "sk-primary"},
        "tools": [{"name": "terminal"}],
        "enable_switch_llm_tool": False,
        "tool_concurrency_limit": 3,
        # A client sends the whole stored condenser, as a stored profile carries
        # it; an omitted key would instead inherit the LLM's token limit.
        "condenser": LLMSummarizingCondenserSettings().model_dump(mode="json"),
        "agent_context": AgentContext(
            skills=[_CATALOG[0]],
            disabled_skills=["denied"],
            system_message_suffix="PROFILE_SUFFIX",
            current_datetime="2020-01-01T00:00",
        ).model_dump(mode="json"),
    }
    before = datetime.now().astimezone()

    _, plan = _launch(None, settings, agent_settings=payload)

    assert isinstance(plan.agent, Agent)
    context = plan.agent.agent_context
    assert context is not None
    assert isinstance(context.current_datetime, datetime)
    assert context.current_datetime >= before
    assert plan.agent.llm.stream is True
    assert plan.launched_profile is None

    store = get_agent_profile_store()
    store.save(_profile("named", llm_profile))
    profile_id = UUID(str(store.list_summaries()[0]["id"]))
    _, profile_plan = _launch(profile_id, settings)
    assert isinstance(profile_plan.agent, Agent)
    assert _agent_fingerprint(plan.agent) == _agent_fingerprint(profile_plan.agent)


def test_materialize_matches_a_real_launch(
    config: Config, settings: PersistedSettings, llm_profile: str
) -> None:
    store = get_agent_profile_store()
    store.save(_profile("named", llm_profile))
    profile_id = UUID(str(store.list_summaries()[0]["id"]))

    client = TestClient(create_app(config))
    response = client.post("/api/agent-profiles/named/materialize")
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True, body["errors"]
    assert body["resolved_skills"] == ["kept"]

    _, plan = _launch(profile_id, settings)
    assert isinstance(plan.settings, OpenHandsAgentSettings)
    launched = plan.settings.model_dump(mode="json")
    previewed = body["resolved_settings"]

    # Only the launch timestamp may differ.
    for dump in (launched, previewed):
        dump["agent_context"].pop("current_datetime")
    assert previewed == launched


def test_inline_profile_draft_launches_and_is_marked_inline(
    settings: PersistedSettings, llm_profile: str
) -> None:
    draft = _profile("draft", llm_profile)
    request = StartConversationRequest(
        agent_profile=draft,
        workspace=LocalWorkspace(working_dir="/tmp/parity"),
    )
    _, plan = prepare_launch_request(
        request,
        cipher=None,
        settings=settings,
        runtime=launch_runtime(
            settings, acp_skill_sourcing="native", browser_available=False
        ),
    )

    assert isinstance(plan.agent, Agent)
    assert plan.launched_profile is not None
    assert plan.launched_profile.inline is True
    assert plan.launched_profile.agent_profile_id == draft.id
    # Nothing was stored.
    assert get_agent_profile_store().list() == []


def test_a_per_launch_llm_override_is_applied_and_recorded(
    settings: PersistedSettings, llm_profile: str
) -> None:
    store = get_agent_profile_store()
    store.save(_profile("named", llm_profile))
    profile_id = UUID(str(store.list_summaries()[0]["id"]))

    _, plan = _launch(
        profile_id,
        settings,
        additions=AgentLaunchAdditions(llm_profile_ref="alternate"),
    )

    assert isinstance(plan.agent, Agent)
    assert plan.agent.llm.model == "claude-opus-5"
    assert plan.launched_profile is not None
    assert plan.launched_profile.llm_profile_ref == "alternate"
    # The stored profile is untouched.
    stored = store.load("named")
    assert isinstance(stored, OpenHandsAgentProfile)
    assert stored.llm_profile_ref == llm_profile


def test_secret_scope_is_enforced_on_the_request(
    settings: PersistedSettings, llm_profile: str
) -> None:
    store = get_agent_profile_store()
    scoped = _profile("scoped", llm_profile).model_copy(
        update={"secret_refs": ["ALLOWED"]}
    )
    store.save(scoped)
    request = StartConversationRequest(
        agent_profile_id=scoped.id,
        workspace=LocalWorkspace(working_dir="/tmp/parity"),
        secrets={
            "ALLOWED": StaticSecret(value=SecretStr("a")),
            "OTHER": StaticSecret(value=SecretStr("b")),
        },
    )
    prepared, plan = prepare_launch_request(
        request,
        cipher=None,
        settings=settings,
        runtime=launch_runtime(settings, acp_skill_sourcing="native"),
    )

    assert set(prepared.secrets) == {"ALLOWED"}
    assert plan.allowed_secrets == frozenset({"ALLOWED"})


def test_a_dangling_reference_fails_the_launch_with_both_names(
    settings: PersistedSettings,
) -> None:
    broken = OpenHandsAgentProfile(
        name="broken", llm_profile_ref="gone", mcp_server_refs=["nope"]
    )
    get_agent_profile_store().save(broken)

    with pytest.raises(UnresolvedProfileReferences) as exc_info:
        _launch(broken.id, settings)

    detail = exc_info.value.to_detail()
    assert detail["code"] == "unresolved_profile_references"
    assert detail["dangling_llm_profile_ref"] == "gone"
    assert detail["dangling_mcp_server_refs"] == ["nope"]


def test_unknown_profile_id_fails_the_launch(settings: PersistedSettings) -> None:
    with pytest.raises(ProfileNotFound):
        _launch(uuid4(), settings)


def test_agent_settings_is_marked_deprecated_in_the_api_schema(
    config: Config,
) -> None:
    schema = create_app(config).openapi()["components"]["schemas"]
    field = schema["StartConversationRequest"]["properties"]["agent_settings"]
    assert field["deprecated"] is True
    assert "agent_profile_id" in field["description"]
