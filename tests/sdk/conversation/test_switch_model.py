import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, LocalConversation
from openhands.sdk.agent import Agent
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.persistence_const import BASE_STATE
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import llm_profile_store
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.testing import TestLLM
from openhands.sdk.utils.cipher import Cipher


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


def test_switch_acp_model_rejects_non_acp_agent():
    """switch_acp_model is only valid for ACP conversations."""
    conv = _make_conversation()  # plain Agent, not ACPAgent
    with pytest.raises(ValueError, match="only supported for ACP"):
        conv.switch_acp_model("haiku")


def _make_acp_conversation(tmp_path) -> tuple[LocalConversation, ACPAgent]:
    """A persisted ACP conversation with a faked-out live session.

    The fake ``_conn`` / ``_executor`` let ``set_acp_model`` issue its
    protocol call without launching a real ACP subprocess.
    """
    agent = ACPAgent(acp_command=["echo", "test"], acp_model="model-a")
    agent._conn = MagicMock()
    agent._session_id = "sess-1"
    agent._agent_name = "codex-acp"
    executor = MagicMock()
    executor.run_async = MagicMock()
    agent._executor = executor
    conv = LocalConversation(
        agent=agent,
        workspace=tmp_path,
        persistence_dir=str(tmp_path / "persist"),
    )
    return conv, agent


def test_switch_acp_model_persists_authoritative_model(tmp_path):
    """A runtime switch persists as the authoritative ``acp_model``.

    Regression for the review finding that re-assigning the same (mutated)
    agent object was an autosave no-op, and that the frozen ``acp_model``
    field — which ``model_post_init`` / ``_start_acp_server`` read on
    reload/resume — stayed at its construction-time value.
    """
    conv, agent = _make_acp_conversation(tmp_path)
    live_conn = agent._conn

    conv.switch_acp_model("model-b")

    # In-memory: agent + state agree on the new model, and the live connection
    # survived the model_copy so the conversation can keep running.
    switched = conv.agent
    assert isinstance(switched, ACPAgent)
    assert switched.acp_model == "model-b"
    assert isinstance(conv.state.agent, ACPAgent)
    assert conv.state.agent.acp_model == "model-b"
    assert switched.llm.model == "model-b"
    assert switched._conn is live_conn
    assert switched._session_id == "sess-1"

    # On disk: base_state.json actually changed (not an autosave no-op), and the
    # persisted agent reconstructs with the switched model as authoritative.
    base_text = conv.state._fs.read(BASE_STATE)
    reloaded = ConversationState.model_validate(json.loads(base_text))
    assert isinstance(reloaded.agent, ACPAgent)
    assert reloaded.agent.acp_model == "model-b"
    # model_post_init derives the sentinel LLM model from the persisted acp_model.
    assert reloaded.agent.llm.model == "model-b"


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


def test_switch_llm_swaps_when_store_empty(empty_profile_store):
    """Real app-server case (#3017): profile is unknown to the sandbox FS,
    the app-server supplies the LLM directly, and the swap succeeds.
    """
    conv = _make_conversation()
    inline = _make_llm("inline-model", "caller-supplied-id")

    conv.switch_llm(inline)

    assert conv.agent.llm.model == "inline-model"
    # State must agree — agent_server reads agent.llm via _state.
    assert conv.state.agent.llm.model == "inline-model"
    # Caller's usage_id is preserved as the registry key.
    assert conv.agent.llm.usage_id == "caller-supplied-id"
    assert conv.llm_registry.get("caller-supplied-id").model == "inline-model"
    # Cache-key must be repinned (regression guard for #2918 on the new path).
    assert conv.agent.llm._prompt_cache_key == str(conv.id)


def test_switch_llm_then_send_message(empty_profile_store):
    """send_message triggers _ensure_agent_ready, which re-registers agent
    LLMs in the registry. switch_llm adds an entry under the caller's
    usage_id; this must not collide with the agent's own LLM
    re-registration on the next send_message().
    """
    conv = _make_conversation()
    conv.switch_llm(_make_llm("inline-model", "x"))
    conv.send_message("hello")


def test_switch_between_two_llms(empty_profile_store):
    """Consecutive switch_llm calls under distinct usage_ids each register
    their own slot and end up as the agent's LLM.
    """
    conv = _make_conversation()

    conv.switch_llm(_make_llm("model-a", "x"))
    assert conv.agent.llm.model == "model-a"

    conv.switch_llm(_make_llm("model-b", "y"))
    assert conv.agent.llm.model == "model-b"


def test_switch_llm_does_not_consult_store(empty_profile_store, monkeypatch):
    """switch_llm must not hit LLMProfileStore.load — the caller is
    authoritative. Guards against a regression where the inline path
    silently falls through to disk IO.
    """
    calls: list[str] = []

    def _spy_load(self, name):
        calls.append(name)
        raise FileNotFoundError(name)

    monkeypatch.setattr(LLMProfileStore, "load", _spy_load)

    conv = _make_conversation()
    conv.switch_llm(_make_llm("inline-model", "x"))

    assert calls == [], f"profile store was consulted: {calls}"


def test_switch_profile_decrypts_with_cipher(tmp_path, monkeypatch):
    """A profile saved with cipher-encrypted secrets must decrypt on switch
    so the agent's LLM ends up with the plaintext API key, not a Fernet
    token (regression for #3164).
    """
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)

    cipher = Cipher("test-key-for-switch-profile")
    store = LLMProfileStore(base_dir=profile_dir)
    store.save(
        "encrypted",
        LLM(
            model="gpt-4o",
            usage_id="encrypted",
            api_key=SecretStr("plaintext-secret"),
        ),
        include_secrets=True,
        cipher=cipher,
    )

    conv = LocalConversation(
        agent=Agent(
            llm=_make_llm("default-model", "test-llm"),
            tools=[],
        ),
        workspace=Path.cwd(),
        cipher=cipher,
    )

    conv.switch_profile("encrypted")

    api_key = conv.agent.llm.api_key
    assert isinstance(api_key, SecretStr)
    assert api_key.get_secret_value() == "plaintext-secret"


def test_switch_profile_delegates_to_switch_llm(profile_store, monkeypatch):
    """switch_profile loads from disk and delegates to switch_llm; the LLM
    handed off carries the canonical ``profile:{name}`` usage_id.
    """
    conv = _make_conversation()
    seen: list[LLM] = []
    real_switch_llm = conv.switch_llm

    def _spy(llm):
        seen.append(llm)
        real_switch_llm(llm)

    monkeypatch.setattr(conv, "switch_llm", _spy)

    conv.switch_profile("fast")

    assert len(seen) == 1
    assert seen[0].usage_id == "profile:fast"
    assert seen[0].model == "fast-model"
