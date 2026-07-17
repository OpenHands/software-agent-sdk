import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, LocalConversation
from openhands.sdk.agent import Agent
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.context.view import View
from openhands.sdk.conversation.persistence_const import BASE_STATE
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent, llm_profile_store
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.testing import TestLLM
from openhands.sdk.utils.cipher import Cipher
from tests.conftest import create_mock_litellm_response


def _make_llm(model: str, usage_id: str) -> LLM:
    return TestLLM.from_messages([], model=model, usage_id=usage_id)


def _message_event(content: str) -> MessageEvent:
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


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


def _make_pre_session_acp_conversation(tmp_path) -> tuple[LocalConversation, ACPAgent]:
    """An ACP conversation created but not yet ``run()``.

    No ``_conn`` / ``_session_id`` / ``_executor`` is wired, so there is no live
    session — ``switch_acp_model`` must defer rather than issue a protocol call.
    """
    agent = ACPAgent(acp_command=["echo", "test"], acp_model="model-a")
    agent._agent_name = "codex-acp"
    conv = LocalConversation(
        agent=agent,
        workspace=tmp_path,
        persistence_dir=str(tmp_path / "persist"),
    )
    return conv, agent


def test_switch_acp_model_before_session_defers_and_persists(tmp_path):
    """A pre-``run()`` switch persists the new model without raising.

    Regression for #3763: ``switch_acp_model`` used to call ``set_acp_model``
    first, which raised ``RuntimeError`` before the first ``run()``, so the
    persist block never ran and the new model was silently lost — the first
    session kept the construction-time ``acp_model``. With no live session the
    live call must be skipped and the value persisted, so session creation on
    the first ``run()`` honors the switched model.
    """
    conv, agent = _make_pre_session_acp_conversation(tmp_path)
    assert not agent.has_live_acp_session

    conv.switch_acp_model("model-b")

    # The authoritative model moved even though no live session was touched.
    switched = conv.agent
    assert isinstance(switched, ACPAgent)
    assert switched.acp_model == "model-b"
    assert not switched.has_live_acp_session
    assert isinstance(conv.state.agent, ACPAgent)
    assert conv.state.agent.acp_model == "model-b"
    # Cold-read hint updated for the chip/picker before any session exists.
    assert conv.state.agent_state["acp_current_model_id"] == "model-b"

    # Survives a restart before the first run: base_state.json carries the
    # switched model, and model_post_init re-derives the sentinel LLM from it,
    # so the first session starts on model-b (acceptance criterion #3).
    base_text = conv.state._fs.read(BASE_STATE)
    reloaded = ConversationState.model_validate(json.loads(base_text))
    assert isinstance(reloaded.agent, ACPAgent)
    assert reloaded.agent.acp_model == "model-b"
    assert reloaded.agent.llm.model == "model-b"


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


def test_switch_acp_model_refreshes_surfaced_current_model_id(tmp_path):
    """After a live switch, the model state surfaced on ``ConversationInfo``
    must reflect the new model, not the stale session-start value.

    Regression for the software-agent-sdk#3347 + #3390 integration: the chip /
    inline picker reads ``ACPAgent.current_model_id`` (and the persisted
    ``acp_current_model_id`` hint on cold reads). Without refreshing both on a
    runtime switch, the chip goes stale exactly when it matters most.
    """
    conv, agent = _make_acp_conversation(tmp_path)
    agent._current_model_id = "model-a"  # what _init captured at session start
    # Deliberately do NOT pre-seed ``acp_current_model_id`` in agent_state:
    # an older/custom server may not have reported a model at init, yet a
    # successful switch is authoritative and must still persist the hint.
    assert "acp_current_model_id" not in conv.state.agent_state

    conv.switch_acp_model("model-b")

    switched = conv.agent
    assert isinstance(switched, ACPAgent)
    # Live PrivateAttr (carried onto the persisted agent by the model_copy).
    assert switched.current_model_id == "model-b"
    # Persisted hint written unconditionally for cold reads before re-init.
    assert conv.state.agent_state["acp_current_model_id"] == "model-b"


def test_switch_acp_model_disarms_discarded_agent_finalizer(tmp_path):
    """The pre-switch agent must not tear down the shared live session.

    Regression: ``switch_acp_model`` swaps in a shallow ``model_copy`` that
    shares ``_conn`` / ``_executor`` / ``_process`` with the old agent. Without
    disarming it first, ``ACPAgent.__del__`` -> ``close()`` on the discarded
    agent closes the connection, kills the subprocess and shuts down the
    executor — out from under the copy, breaking the next turn.
    """
    conv, old_agent = _make_acp_conversation(tmp_path)
    live_conn = old_agent._conn
    live_executor = old_agent._executor
    lifecycle = MagicMock()
    lifecycle.path = tmp_path / "auth.json"
    lifecycle.authenticated = True
    old_agent._file_credential_lifecycles["CODEX_AUTH_JSON"] = lifecycle
    credential_lock = old_agent._file_credential_lock

    conv.switch_acp_model("model-b")

    # The copy took over the live runtime...
    switched = conv.agent
    assert isinstance(switched, ACPAgent)
    assert switched._conn is live_conn
    assert switched._executor is live_executor
    assert switched._file_credential_lifecycles == {"CODEX_AUTH_JSON": lifecycle}
    assert switched._file_credential_lock is credential_lock

    # ...and the discarded agent's finalizer was disarmed (marked closed)
    # WITHOUT clearing its runtime references — an in-flight ask_agent()/fork
    # still holding the old agent keeps a valid connection.
    assert old_agent._closed is True
    assert old_agent._conn is live_conn
    assert old_agent._executor is live_executor
    assert old_agent._file_credential_lifecycles == {}

    # Simulating GC (__del__ -> close()) on the disarmed old agent is a no-op:
    # the copy's shared connection/executor are left intact.
    live_executor.run_async.reset_mock()
    old_agent.close()
    live_executor.run_async.assert_not_called()
    live_executor.close.assert_not_called()
    switched.release_runtime()


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
    assert conv.agent.llm._call_context.prompt_cache_key == expected

    conv.switch_profile("fast")
    assert conv.agent.llm._call_context.prompt_cache_key == expected

    conv.switch_profile("slow")
    assert conv.agent.llm._call_context.prompt_cache_key == expected

    # Switching back to a cached registry entry must still carry the key.
    conv.switch_profile("fast")
    assert conv.agent.llm._call_context.prompt_cache_key == expected


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
    assert conv.agent.llm._call_context.prompt_cache_key == str(conv.id)


def test_switch_llm_refreshes_llm_condenser_credentials(
    empty_profile_store, tmp_path, monkeypatch
):
    """A mid-session LLM switch must also refresh the default condenser LLM.

    The condenser owns a separate copy of the agent LLM. If the agent LLM is
    switched but that copy is left behind, normal turns can keep working while
    the next condensation request still calls the old no-credential model.
    """
    initial_llm = LLM(model="litellm_proxy/old-model", usage_id="default")
    initial_condenser_llm = initial_llm.model_copy(update={"usage_id": "condenser"})
    initial_condenser_llm.reset_metrics()
    condenser = LLMSummarizingCondenser(
        llm=initial_condenser_llm,
        max_size=100,
        keep_first=2,
    )
    conv = LocalConversation(
        agent=Agent(llm=initial_llm, condenser=condenser, tools=[]),
        workspace=tmp_path,
    )
    conv._ensure_agent_ready()

    switched_llm = LLM(
        model="litellm_proxy/new-model",
        api_key=SecretStr("new-test-key"),
        usage_id="profile:new",
    )

    conv.switch_llm(switched_llm)

    assert conv.agent.llm.model == "litellm_proxy/new-model"
    assert isinstance(conv.agent.condenser, LLMSummarizingCondenser)
    assert isinstance(conv.state.agent.condenser, LLMSummarizingCondenser)

    condenser_llm = conv.agent.condenser.llm
    state_condenser_llm = conv.state.agent.condenser.llm
    assert condenser_llm is not initial_condenser_llm
    assert condenser_llm.model == "litellm_proxy/new-model"
    assert condenser_llm.usage_id == "condenser"
    assert isinstance(condenser_llm.api_key, SecretStr)
    assert condenser_llm.api_key.get_secret_value() == "new-test-key"
    assert state_condenser_llm.model == condenser_llm.model
    assert state_condenser_llm.api_key == condenser_llm.api_key
    assert condenser_llm.metrics is not conv.agent.llm.metrics
    assert condenser_llm._telemetry is not None

    async def _fake_acompletion(**kwargs):
        return create_mock_litellm_response(
            content="condensed summary",
            model=kwargs["model"],
        )

    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_acompletion", _fake_acompletion)

    response = asyncio.run(
        condenser_llm.acompletion(
            [Message(role="user", content=[TextContent(text="summarize")])]
        )
    )

    content = response.message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "condensed summary"


def test_switch_llm_condenser_can_generate_condensation(
    empty_profile_store, tmp_path, monkeypatch
):
    initial_llm = LLM(model="litellm_proxy/old-model", usage_id="default")
    condenser = LLMSummarizingCondenser(
        llm=initial_llm.model_copy(update={"usage_id": "condenser"}),
        max_size=6,
        keep_first=1,
    )
    conv = LocalConversation(
        agent=Agent(llm=initial_llm, condenser=condenser, tools=[]),
        workspace=tmp_path,
    )
    conv._ensure_agent_ready()

    switched_llm = LLM(
        model="litellm_proxy/new-model",
        api_key=SecretStr("new-test-key"),
        usage_id="profile:new",
    )
    conv.switch_llm(switched_llm)

    def _fake_completion(**kwargs):
        return create_mock_litellm_response(
            content="condensed summary",
            model=kwargs["model"],
        )

    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_completion", _fake_completion)

    assert isinstance(conv.agent.condenser, LLMSummarizingCondenser)
    condensation = conv.agent.condenser.get_condensation(
        View.from_events([_message_event(f"event {i}") for i in range(12)]),
        agent_llm=conv.agent.llm,
    )

    assert condensation.summary == "condensed summary"
    assert len(condensation.forgotten_event_ids) > 0


def test_switch_llm_preserves_independent_condenser_profile(
    empty_profile_store, tmp_path
):
    initial_llm = LLM(
        model="litellm_proxy/agent-old",
        api_key=SecretStr("agent-old-key"),
        usage_id="default",
    )
    independent_condenser_llm = LLM(
        model="litellm_proxy/condenser-profile",
        api_key=SecretStr("condenser-key"),
        usage_id="condenser",
    )
    condenser = LLMSummarizingCondenser(
        llm=independent_condenser_llm,
        max_size=100,
        keep_first=2,
    )
    conv = LocalConversation(
        agent=Agent(llm=initial_llm, condenser=condenser, tools=[]),
        workspace=tmp_path,
    )
    conv._ensure_agent_ready()

    conv.switch_llm(
        LLM(
            model="litellm_proxy/agent-new",
            api_key=SecretStr("agent-new-key"),
            usage_id="profile:new",
        )
    )

    assert isinstance(conv.agent.condenser, LLMSummarizingCondenser)
    condenser_llm = conv.agent.condenser.llm
    assert condenser_llm.model == "litellm_proxy/condenser-profile"
    assert isinstance(condenser_llm.api_key, SecretStr)
    assert condenser_llm.api_key.get_secret_value() == "condenser-key"


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


def test_duplicate_usage_ids_in_registration_loop_are_silently_deduped(tmp_path):
    """Regression: if agent LLM and condenser LLM both carry the same
    usage_id (as happens when a conversation is deserialised from a
    base_state.json written before #3368), _ensure_agent_ready() must
    NOT raise ValueError.  The first-write-wins contract means only
    the agent LLM entry is registered; the condenser duplicate is
    silently skipped.
    """
    # Simulate the broken persisted state: condenser inherits the agent LLM's usage_id
    agent_llm = LLM(
        model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="default"
    )
    condenser_llm = agent_llm.model_copy()  # inherits usage_id="default"
    condenser = LLMSummarizingCondenser(llm=condenser_llm, max_size=100, keep_first=2)
    agent = Agent(llm=agent_llm, condenser=condenser, tools=[])

    conv = LocalConversation(agent=agent, workspace=tmp_path)

    # Must not raise ValueError("Usage ID 'default' already exists in registry")
    conv._ensure_agent_ready()

    # First-write-wins: only one entry under the shared usage_id
    assert conv.llm_registry.list_usage_ids().count("default") == 1


def test_switch_llm_tool_during_arun_does_not_deadlock(profile_store, tmp_path):
    """Regression for #3485: a ``switch_llm`` tool call during ``arun()`` must
    not deadlock the conversation runtime.

    ``arun()`` holds the ConversationState lock across the (async) agent step
    while tools execute on worker threads. ``switch_llm()`` used to re-acquire
    that lock from the tool worker thread, which deadlocked against the run
    loop that already held it and was blocked awaiting the tool — no
    observation was ever emitted and the conversation hung in ``running``.

    The agent emits the switch and a finish in a single step, so the switch
    happens mid-step (the deadlock site) and the run can complete.
    """
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    MessageToolCall(
                        id="switch_1",
                        name="switch_llm",
                        arguments='{"profile_name": "fast", "reason": "test"}',
                        origin="completion",
                    ),
                    MessageToolCall(
                        id="finish_1",
                        name="finish",
                        arguments='{"message": "done"}',
                        origin="completion",
                    ),
                ],
            ),
        ],
        model="default-model",
        usage_id="test-llm",
    )

    # Resolve the tools from BUILT_IN_TOOL_CLASSES (no global registry writes).
    agent = Agent(llm=llm, include_default_tools=["FinishTool", "SwitchLLMTool"])
    conv = LocalConversation(agent=agent, workspace=tmp_path, visualizer=None)
    conv.send_message("please switch")

    # Bounded so a regression surfaces as a fast test failure rather than a
    # hung suite. On the fixed path arun() completes near-instantly.
    asyncio.run(asyncio.wait_for(conv.arun(), timeout=10))

    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
    # The switch took effect: the agent now carries the 'fast' profile's model.
    assert conv.agent.llm.model == "fast-model"


def test_switch_llm_to_subscription_profile_disables_condenser(
    monkeypatch, empty_profile_store
):
    import openhands.sdk.conversation.impl.local_conversation as local_conversation

    condenser = LLMSummarizingCondenser(
        llm=_make_llm("condenser-model", "condenser"),
        max_size=100,
        keep_first=5,
    )
    conv = LocalConversation(
        agent=Agent(
            llm=_make_llm("default-model", "test-llm"),
            tools=[],
            condenser=condenser,
        ),
        workspace=Path.cwd(),
    )
    assert conv.agent.condenser is condenser

    def fake_create_subscription_llm_from_config(llm: LLM) -> LLM:
        runtime = llm.model_copy()
        if llm.auth_type == "subscription":
            runtime._is_subscription = True
        return runtime

    monkeypatch.setattr(
        local_conversation,
        "create_subscription_llm_from_config",
        fake_create_subscription_llm_from_config,
    )

    conv.switch_llm(
        LLM(
            model="gpt-5.2-codex",
            usage_id="profile:codex",
            auth_type="subscription",
            subscription_vendor="openai",
        )
    )

    assert conv.agent.llm.is_subscription
    assert conv.agent.condenser is None
    assert conv.state.agent.condenser is None

    conv.switch_llm(_make_llm("regular-model", "regular"))

    assert conv.agent.llm.model == "regular-model"
    assert conv.agent.condenser is condenser
    assert conv.state.agent.condenser is condenser
