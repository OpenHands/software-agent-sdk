from pathlib import Path

import pytest

from openhands.sdk import LLM, LocalConversation
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
def profile_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LLMProfileStore:
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)

    store = LLMProfileStore(base_dir=profile_dir)
    store.save("fast", _make_llm("fast-model", "fast"))
    store.save("slow", _make_llm("slow-model", "slow"))
    return store


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


def test_switch_llm_tool_switches_conversation_profile(profile_store):
    conversation = _make_conversation()

    observation = conversation.execute_tool(
        "switch_llm",
        SwitchLLMAction(profile_name="fast", reason="Need a faster profile."),
    )

    assert isinstance(observation, SwitchLLMObservation)
    assert not observation.is_error
    assert observation.profile_name == "fast"
    assert observation.active_model == "fast-model"
    assert "active model 'fast-model'" in observation.text
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
    assert observation.active_model is None
    assert "was not found" in observation.text
    assert conversation.agent.llm.model == "default-model"
    assert conversation.state.agent.llm.model == "default-model"
