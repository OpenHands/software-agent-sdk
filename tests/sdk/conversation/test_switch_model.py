from pathlib import Path

import pytest

from openhands.sdk import LLM, LocalConversation
from openhands.sdk.agent import Agent
from openhands.sdk.llm import llm_profile_store
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
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
    store.save("fast", _make_llm("fast-model", "fast"))
    store.save("slow", _make_llm("slow-model", "slow"))
    return store


def _make_conversation() -> LocalConversation:
    return LocalConversation(
        agent=Agent(
            llm=_make_llm("default-model", "test-llm"),
            tools=[],
        ),
        workspace=Path.cwd(),
    )


def test_switch_profile(profile_store):
    """switch_profile switches the agent's LLM."""
    conv = _make_conversation()
    conv.switch_profile("fast")
    assert conv.agent.llm.model == "fast-model"
    conv.switch_profile("slow")
    assert conv.agent.llm.model == "slow-model"


def test_switch_profile_updates_state(profile_store):
    """switch_profile updates conversation state agent."""
    conv = _make_conversation()
    conv.switch_profile("fast")
    assert conv.state.agent.llm.model == "fast-model"


def test_switch_between_profiles(profile_store):
    """Switch fast -> slow -> fast, verify model changes each time."""
    conv = _make_conversation()

    conv.switch_profile("fast")
    assert conv.agent.llm.model == "fast-model"

    conv.switch_profile("slow")
    assert conv.agent.llm.model == "slow-model"

    conv.switch_profile("fast")
    assert conv.agent.llm.model == "fast-model"


def test_switch_reuses_registry_entry(profile_store):
    """Switching back to a profile reuses the same registry LLM object."""
    conv = _make_conversation()

    conv.switch_profile("fast")
    llm_first = conv.llm_registry.get("profile:fast")

    conv.switch_profile("slow")
    conv.switch_profile("fast")
    llm_second = conv.llm_registry.get("profile:fast")

    assert llm_first is llm_second


def test_switch_nonexistent_raises(profile_store):
    """Switching to a nonexistent profile raises FileNotFoundError."""
    conv = _make_conversation()
    with pytest.raises(FileNotFoundError):
        conv.switch_profile("nonexistent")
    assert conv.agent.llm.model == "default-model"
    assert conv.state.agent.llm.model == "default-model"


def test_switch_profile_preserves_prompt_cache_key(profile_store):
    """Regression test for #2918: switch_profile must repin _prompt_cache_key."""
    conv = _make_conversation()
    expected = str(conv.id)
    assert conv.agent.llm._prompt_cache_key == expected

    conv.switch_profile("fast")
    assert conv.agent.llm._prompt_cache_key == expected

    conv.switch_profile("slow")
    assert conv.agent.llm._prompt_cache_key == expected

    # Switching back to a cached registry entry must still carry the key.
    conv.switch_profile("fast")
    assert conv.agent.llm._prompt_cache_key == expected


def test_switch_then_send_message(profile_store):
    """switch_profile followed by send_message doesn't crash on registry collision."""
    conv = _make_conversation()
    conv.switch_profile("fast")
    # send_message triggers _ensure_agent_ready which re-registers agent LLMs;
    # the switched LLM must not cause a duplicate registration error.
    conv.send_message("hello")
