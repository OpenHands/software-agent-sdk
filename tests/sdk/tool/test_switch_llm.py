from pathlib import Path

import pytest

from openhands.sdk import LLM, LocalConversation, OpenHandsAgentSettings
from openhands.sdk.agent import Agent
from openhands.sdk.llm import llm_profile_store
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins import (
    SwitchLLMAction,
    SwitchLLMObservation,
    SwitchLLMTool,
)


def _make_llm(model: str, usage_id: str) -> LLM:
    return TestLLM.from_messages([], model=model, usage_id=usage_id)


@pytest.fixture()
def empty_profile_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> LLMProfileStore:
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)
    return LLMProfileStore(base_dir=profile_dir)


@pytest.fixture()
def profile_store(empty_profile_store: LLMProfileStore) -> LLMProfileStore:
    empty_profile_store.save("fast", _make_llm("fast-model", "fast"))
    empty_profile_store.save("slow", _make_llm("slow-model", "slow"))
    return empty_profile_store


def _make_conversation() -> LocalConversation:
    return LocalConversation(
        agent=Agent(
            llm=_make_llm("default-model", "default"),
            tools=[],
            include_default_tools=["SwitchLLMTool"],
        ),
        workspace=Path.cwd(),
    )


def test_switch_llm_tool_description_lists_available_profiles(profile_store):
    tool = SwitchLLMTool.create()[0]

    assert "Available LLM profiles:" in tool.description
    assert "- fast" in tool.description
    assert "- slow" in tool.description


def test_agent_settings_includes_switch_llm_tool_when_profiles_exist(profile_store):
    agent = OpenHandsAgentSettings(
        llm=_make_llm("default-model", "default")
    ).create_agent()

    assert "SwitchLLMTool" in agent.include_default_tools

    conversation = LocalConversation(agent=agent, workspace=Path.cwd())
    conversation._ensure_agent_ready()
    assert "switch_llm" in agent.tools_map


def test_agent_settings_omits_switch_llm_tool_when_disabled(profile_store):
    agent = OpenHandsAgentSettings(
        llm=_make_llm("default-model", "default"),
        enable_switch_llm_tool=False,
    ).create_agent()

    assert "SwitchLLMTool" not in agent.include_default_tools

    conversation = LocalConversation(agent=agent, workspace=Path.cwd())
    conversation._ensure_agent_ready()
    assert "switch_llm" not in agent.tools_map


def test_agent_settings_includes_switch_llm_tool_without_profiles(empty_profile_store):
    agent = OpenHandsAgentSettings(
        llm=_make_llm("default-model", "default")
    ).create_agent()

    assert "SwitchLLMTool" in agent.include_default_tools

    conversation = LocalConversation(agent=agent, workspace=Path.cwd())
    conversation._ensure_agent_ready()
    assert "switch_llm" in agent.tools_map


def test_switch_llm_tool_switches_conversation_profile(profile_store):
    conversation = _make_conversation()

    observation = conversation.execute_tool(
        "switch_llm",
        SwitchLLMAction(profile_name="fast", reason="Need a faster profile."),
    )

    assert isinstance(observation, SwitchLLMObservation)
    assert not observation.is_error
    assert observation.profile_name == "fast"
    assert observation.reason == "Need a faster profile."
    assert observation.active_model == "fast-model"
    assert "active model 'fast-model'" in observation.text
    assert "Reason: Need a faster profile." in observation.text
    assert "Need a faster profile." in observation.visualize.plain
    assert conversation.agent.llm.model == "fast-model"
    assert conversation.state.agent.llm.model == "fast-model"


def test_switch_llm_tool_reports_missing_profile(profile_store):
    conversation = _make_conversation()

    observation = conversation.execute_tool(
        "switch_llm",
        SwitchLLMAction(profile_name="missing", reason="Try another model."),
    )

    assert isinstance(observation, SwitchLLMObservation)
    assert observation.is_error
    assert observation.profile_name == "missing"
    assert observation.reason == "Try another model."
    assert observation.active_model is None
    assert "was not found" in observation.text
    assert conversation.agent.llm.model == "default-model"
    assert conversation.state.agent.llm.model == "default-model"


def test_switch_llm_tool_reports_unexpected_profile_load_error(
    profile_store, monkeypatch: pytest.MonkeyPatch
):
    conversation = _make_conversation()

    def _raise_permission_error(profile_name: str) -> None:
        raise PermissionError(f"Cannot read {profile_name}")

    monkeypatch.setattr(conversation, "switch_profile", _raise_permission_error)

    observation = conversation.execute_tool(
        "switch_llm",
        SwitchLLMAction(profile_name="fast", reason="Need access to Claude."),
    )

    assert isinstance(observation, SwitchLLMObservation)
    assert observation.is_error
    assert observation.profile_name == "fast"
    assert observation.reason == "Need access to Claude."
    assert observation.active_model is None
    assert "PermissionError" in observation.text
    assert "Cannot read fast" in observation.text
    assert conversation.agent.llm.model == "default-model"
    assert conversation.state.agent.llm.model == "default-model"
