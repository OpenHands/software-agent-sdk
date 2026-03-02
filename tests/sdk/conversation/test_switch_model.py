from pathlib import Path

import pytest

from openhands.sdk import LLM, LocalConversation
from openhands.sdk.agent import Agent
from openhands.sdk.conversation.switch_model_handler import SwitchModelHandler
from openhands.sdk.llm import TextContent, llm_profile_store
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.llm_registry import LLMRegistry
from openhands.sdk.testing import TestLLM


def _make_llm(model: str, usage_id: str) -> LLM:
    return TestLLM.from_messages([], model=model, usage_id=usage_id)


@pytest.fixture()
def profile_store(tmp_path, monkeypatch):
    """
    Create a temp profile store with 'fast' and
    'slow' profiles saved via _make_llm.
    """

    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)

    store = LLMProfileStore(base_dir=profile_dir)
    store.save("fast", _make_llm("fast-model", "tmp"))
    store.save("slow", _make_llm("slow-model", "tmp"))
    return store


def _make_conversation(enable_switch: bool) -> LocalConversation:
    return LocalConversation(
        agent=Agent(
            llm=_make_llm("default-model", "test-llm"),
            tools=[],
        ),
        allow_model_switching=enable_switch,
        workspace=Path.cwd(),
    )


@pytest.mark.parametrize(
    "command, expected",
    [
        ("hello world", None),
        ("/modelfoo", None),
        ("/model", ("", None)),
        ("/model   ", ("", None)),
        ("/model fast", ("fast", None)),
        ("/model fast hello world", ("fast", "hello world")),
        ("  /model   fast   hi  ", ("fast", "hi")),
        ("/model my-profile.json", ("my-profile.json", None)),
    ],
)
def test_parse_model_command(command: str, expected: tuple[str, str | None]):
    assert SwitchModelHandler.parse(command) == expected


def test_switch_model_false(profile_store):
    """
    If enable switch is False, then we should not
    be able to switch the model.
    """
    conv = _make_conversation(enable_switch=False)
    conv.send_message("/model fast")
    assert conv.agent.llm.model == "default-model"
    conv.send_message("/model slow")
    assert conv.agent.llm.model == "default-model"


def test_switch_model_true(profile_store):
    """/model fast switches the agent's LLM."""
    conv = _make_conversation(enable_switch=True)
    conv.send_message("/model fast")
    assert conv.agent.llm.model == "fast-model"
    conv.send_message("/model slow")
    assert conv.agent.llm.model == "slow-model"


def test_switch_model_with_message(profile_store):
    """/model fast hello switches model AND sends the remaining text."""
    conv = _make_conversation(enable_switch=True)
    conv.send_message("/model fast hello world")

    assert conv.agent.llm.model == "fast-model"

    user_msg = conv.state.events[-1]
    assert user_msg.source == "user"
    llm_msg = getattr(user_msg, "llm_message")
    msg = llm_msg.content[0]
    assert isinstance(msg, TextContent)
    assert msg.text == "hello world"


def test_switch_between_profiles(profile_store):
    """Switch fast -> slow -> fast, verify model changes each time."""
    conv = _make_conversation(enable_switch=True)

    conv.send_message("/model fast")
    assert conv.agent.llm.model == "fast-model"

    conv.send_message("/model slow")
    assert conv.agent.llm.model == "slow-model"

    conv.send_message("/model fast")
    assert conv.agent.llm.model == "fast-model"


def test_switch_reuses_registry_entry(profile_store):
    """Switching back to a profile reuses the same registry LLM object."""
    conv = _make_conversation(enable_switch=True)

    conv.send_message("/model fast")
    llm_first = conv.llm_registry.get("fast")

    conv.send_message("/model slow")
    conv.send_message("/model fast")
    llm_second = conv.llm_registry.get("fast")

    assert llm_first is llm_second


class TestModelCommandHandlerUnit:
    """Test ModelCommandHandler without a full LocalConversation."""

    def test_switch_returns_new_agent(self, profile_store):
        registry = LLMRegistry()
        handler = SwitchModelHandler(profile_store, registry)
        agent = Agent(llm=_make_llm("default-model", "test-llm"), tools=[])

        new_agent = handler.switch(agent, "fast")
        assert new_agent.llm.model == "fast-model"
        # Original agent unchanged
        assert agent.llm.model == "default-model"

    def test_switch_reuses_registry(self, profile_store):
        registry = LLMRegistry()
        handler = SwitchModelHandler(profile_store, registry)
        agent = Agent(llm=_make_llm("default-model", "test-llm"), tools=[])

        handler.switch(agent, "fast")
        llm_first = registry.get("fast")

        handler.switch(agent, "fast")
        llm_second = registry.get("fast")

        assert llm_first is llm_second

    def test_switch_not_found(self, profile_store):
        registry = LLMRegistry()
        handler = SwitchModelHandler(profile_store, registry)
        agent = Agent(llm=_make_llm("default-model", "test-llm"), tools=[])

        with pytest.raises(FileNotFoundError, match="not found"):
            handler.switch(agent, "nonexistent")

    def test_model_info_contains_current_model(self, profile_store):
        registry = LLMRegistry()
        handler = SwitchModelHandler(profile_store, registry)
        agent = Agent(llm=_make_llm("my-model", "test-llm"), tools=[])

        info = handler.get_profiles_info_message(agent.llm)
        assert "Current model: my-model\nAvailable profiles: fast, slow" == info
