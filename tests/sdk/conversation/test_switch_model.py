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


@pytest.fixture()
def empty_profile_store(tmp_path, monkeypatch):
    """Empty profile dir — simulates the agent-server sandbox where the
    app-server has never uploaded profile JSON. This is the real failure
    mode #3017 is fixing.
    """
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)
    return profile_dir


def test_switch_profile_inline_llm_when_store_is_empty(empty_profile_store):
    """Real app-server case: profile is unknown to the sandbox FS, the
    app-server supplies the LLM inline, and the swap succeeds without a 404.
    """
    conv = _make_conversation()
    inline = _make_llm("inline-model", "caller-supplied-id")

    # Without `llm`, this would raise FileNotFoundError.
    conv.switch_profile("from-app-server", llm=inline)

    assert conv.agent.llm.model == "inline-model"
    # State must agree — agent_server reads agent.llm via _state.
    assert conv.state.agent.llm.model == "inline-model"
    # usage_id is canonicalised so future store-path lookups hit the cache.
    assert conv.agent.llm.usage_id == "profile:from-app-server"
    assert conv.llm_registry.get("profile:from-app-server").model == "inline-model"
    # Cache-key must be repinned (regression guard for #2918 on the new path).
    assert conv.agent.llm._prompt_cache_key == str(conv.id)


def test_switch_profile_inline_llm_then_send_message(empty_profile_store):
    """Mirrors test_switch_then_send_message for the inline path.

    send_message triggers _ensure_agent_ready, which re-registers agent LLMs
    in the registry. The inline path adds an entry under
    ``profile:{name}``; this must not collide with the agent's own LLM
    re-registration on the next send_message().
    """
    conv = _make_conversation()
    conv.switch_profile("from-app-server", llm=_make_llm("inline-model", "x"))
    conv.send_message("hello")


def test_switch_between_two_inline_profiles(empty_profile_store):
    """The /model command flow: swap profile A inline, then profile B inline.

    Each profile is registered under its own ``profile:{name}`` slot, so
    consecutive inline swaps for *different* names must not collide in the
    registry.
    """
    conv = _make_conversation()

    conv.switch_profile("a", llm=_make_llm("model-a", "x"))
    assert conv.agent.llm.model == "model-a"

    conv.switch_profile("b", llm=_make_llm("model-b", "y"))
    assert conv.agent.llm.model == "model-b"

    # Switching back to "a" without re-supplying llm hits the cached entry.
    conv.switch_profile("a")
    assert conv.agent.llm.model == "model-a"


def test_switch_profile_inline_llm_cache_wins_on_repeat_call(empty_profile_store):
    """Locks in the chosen semantics: when ``profile:{name}`` is already
    cached, the cached LLM wins and a fresh ``llm=`` is silently ignored.

    This is symmetric with the store-path behaviour
    (``test_switch_reuses_registry_entry``). Documenting it as a test
    so a future refactor can't change the contract unnoticed.
    """
    conv = _make_conversation()

    conv.switch_profile("inline", llm=_make_llm("first-model", "x"))
    # Second call with a different LLM under the same profile name —
    # registry cache is authoritative; the new LLM is dropped.
    conv.switch_profile("inline", llm=_make_llm("second-model", "y"))

    assert conv.agent.llm.model == "first-model"


def test_switch_profile_inline_llm_does_not_consult_store(
    empty_profile_store, monkeypatch
):
    """Inline LLM must not hit LLMProfileStore.load — even if the store
    would otherwise have content. Guards against a regression where the
    caller-authoritative path silently falls through to disk IO.
    """
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore

    calls: list[str] = []

    def _spy_load(self, name):
        calls.append(name)
        raise FileNotFoundError(name)  # would 404 — must not be reached

    monkeypatch.setattr(LLMProfileStore, "load", _spy_load)

    conv = _make_conversation()
    conv.switch_profile("inline", llm=_make_llm("inline-model", "x"))

    assert calls == [], f"profile store was consulted: {calls}"
