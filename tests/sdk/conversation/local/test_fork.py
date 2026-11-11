"""Tests for Conversation.fork() primitive."""

import json
import tempfile
import threading
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Self

import pytest
from pydantic import PrivateAttr, SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.secret import SecretSource
from openhands.sdk.security.confirmation_policy import ConfirmRisky
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.security.toolshield_llm_analyzer import (
    ToolShieldLLMSecurityAnalyzer,
)
from openhands.sdk.tool import Action, Observation, ToolDefinition, ToolExecutor
from openhands.sdk.utils.cipher import Cipher


def _agent() -> Agent:
    return Agent(
        llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test"),
        tools=[],
    )


def _msg(event_id: str, text: str = "hi") -> MessageEvent:
    return MessageEvent(
        id=event_id,
        llm_message=Message(role="user", content=[TextContent(text=text)]),
        source="user",
    )


class _LockedSecretSource(SecretSource):
    """A secret source that holds something a deep copy cannot copy."""

    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def get_value(self) -> str:
        with self._lock:
            return "locked-source-value"


class ForkCopyMockAction(Action):
    command: str = "test"


class ForkCopyMockObservation(Observation):
    pass


class ForkCopyNonPicklableExecutor(
    ToolExecutor[ForkCopyMockAction, ForkCopyMockObservation]
):
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def __call__(
        self,
        action: ForkCopyMockAction,
        conversation: LocalConversation | None = None,
    ) -> ForkCopyMockObservation:
        return ForkCopyMockObservation.from_text(action.command)


class ForkCopyNonPicklableTool(
    ToolDefinition[ForkCopyMockAction, ForkCopyMockObservation]
):
    @classmethod
    def create(cls, *args, **kwargs) -> Sequence[Self]:
        return [
            cls(
                description="test tool with a non-picklable executor",
                action_type=ForkCopyMockAction,
                observation_type=ForkCopyMockObservation,
                executor=ForkCopyNonPicklableExecutor(),
            )
        ]


def test_fork_creates_new_id():
    """Forked conversation must have a distinct ID."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        assert fork.id != src.id
        assert isinstance(fork.id, uuid.UUID)


def test_fork_with_explicit_id():
    """Explicit conversation_id is honoured."""
    custom_id = uuid.uuid4()
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork(conversation_id=custom_id)

        assert fork.id == custom_id


def test_fork_copies_events():
    """Events from the source must appear in the fork."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src.state.events.append(_msg("evt-1", "hello"))
        src.state.events.append(_msg("evt-2", "world"))

        fork = src.fork()

        # The fork should have at least the events we added
        fork_ids = [e.id for e in fork.state.events]
        assert "evt-1" in fork_ids
        assert "evt-2" in fork_ids


def test_fork_copies_system_prompt_event_with_non_picklable_executor():
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        source_tool = ForkCopyNonPicklableTool.create()[0]
        src.state.events.append(
            SystemPromptEvent(
                system_prompt=TextContent(text="test system prompt"),
                tools=[source_tool],
            )
        )

        fork = src.fork()

        source_event = src.state.events[0]
        fork_event = fork.state.events[0]
        assert isinstance(source_event, SystemPromptEvent)
        assert isinstance(fork_event, SystemPromptEvent)
        assert fork_event is not source_event
        assert source_event.tools[0].executor is not None
        assert fork_event.tools[0].executor is None
        assert fork_event.tools[0].action_type is ForkCopyMockAction


def test_fork_source_unmodified():
    """Appending to the fork must not affect the source."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src.state.events.append(_msg("src-evt"))
        src_event_count = len(src.state.events)

        fork = src.fork()
        fork.state.events.append(_msg("fork-only"))

        # Source should not grow
        assert len(src.state.events) == src_event_count


def test_fork_execution_status_is_idle():
    """Forked conversation starts in idle status."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        assert fork.state.execution_status == ConversationExecutionStatus.IDLE


def test_fork_resets_metrics_by_default():
    """By default, metrics on the fork should be fresh (empty)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            max_budget_per_run=2.0,
        )
        src.max_budget_per_run = 3.0
        fork = src.fork()

        assert fork.max_budget_per_run == 3.0
        saved = json.loads(Path(tmpdir, fork.id.hex, "base_state.json").read_text())
        assert saved["max_budget_per_run"] == 3.0
        combined = fork.state.stats.get_combined_metrics()
        assert combined.accumulated_cost == 0


def test_fork_preserves_metrics_when_requested():
    """When reset_metrics=False the fork should carry over stats."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            max_budget_per_run=2.0,
        )
        # Inject a non-zero metric
        from openhands.sdk.llm.utils.metrics import Metrics

        m = Metrics()
        m.accumulated_cost = 1.5
        src._state.stats.usage_to_metrics["test"] = m

        fork = src.fork(reset_metrics=False)

        assert fork.max_budget_per_run == 2.0
        combined = fork.state.stats.get_combined_metrics()
        assert combined.accumulated_cost == pytest.approx(1.5)


def test_fork_copies_agent_state():
    """agent_state dict should be carried over to the fork."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src._state.agent_state = {"key": "value"}

        fork = src.fork()

        assert fork.state.agent_state == {"key": "value"}
        # Mutation on fork should not affect source
        fork._state.agent_state = {**fork._state.agent_state, "new": True}
        assert "new" not in src._state.agent_state


def test_fork_accepts_replacement_agent():
    """Providing an agent kwarg replaces the source agent in the fork."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        alt_agent = Agent(
            llm=LLM(
                model="gpt-4o",
                api_key=SecretStr("other-key"),
                usage_id="alt",
            ),
            tools=[],
        )

        fork = src.fork(agent=alt_agent)

        assert fork.agent.llm.model == "gpt-4o"
        # Source should keep its original agent
        assert src.agent.llm.model == "gpt-4o-mini"


def test_fork_with_tags():
    """Tags should be passed through to the fork."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork(tags={"env": "test"})

        assert fork.state.tags.get("env") == "test"


@pytest.mark.parametrize("tags", [{}, {"purpose": "debugging"}, {"title": "user tag"}])
def test_fork_with_title_is_ignored(tags: dict[str, str]):
    """Passing title is accepted for signature compat but does not pollute tags."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork(title="My Fork", tags=tags)

        assert dict(fork.state.tags) == tags


def test_fork_shares_workspace():
    """Fork should reuse the same workspace as the source."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        assert fork.workspace.working_dir == src.workspace.working_dir


def test_fork_event_deep_copy_isolation():
    """Mutating an event object in the fork must not affect the source."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src.state.events.append(_msg("deep-evt", "original"))

        fork = src.fork()

        # The fork event is a different object
        src_evt = src.state.events[0]
        fork_evt = fork.state.events[0]
        assert src_evt is not fork_evt

        # Mutating the fork event should not change the source
        assert fork_evt.llm_message.content[0].text == "original"  # type: ignore[union-attr]
        fork_evt.llm_message.content[0].text = "mutated"  # type: ignore[union-attr]
        assert src_evt.llm_message.content[0].text == "original"  # type: ignore[union-attr]


def test_fork_persistence_path_no_doubling():
    """Fork persistence dir must be a sibling of source, not nested inside it.

    Regression test: fork() previously computed the persistence path with
    the conversation hex appended, but __init__ also appends it via
    get_persistence_dir(), leading to /base/FORK_HEX/FORK_HEX.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        assert src._state.persistence_dir is not None
        assert fork._state.persistence_dir is not None
        src_path = Path(src._state.persistence_dir)
        fork_path = Path(fork._state.persistence_dir)

        # Both should live directly under the same base directory
        assert src_path.parent == fork_path.parent
        # The fork dir should be <base>/<fork_id_hex>, not doubled
        assert fork_path.name == fork.id.hex


def test_fork_persisted_events_survive_reload():
    """Events persisted by fork() should be loadable from the fork dir.

    This validates the path-doubling fix end-to-end: if the fork wrote
    events to the wrong directory, resuming from the correct path would
    see zero events.
    """
    # Event IDs must be hex+dash, ≥8 chars to match EVENT_NAME_RE.
    evt_id_1 = uuid.uuid4().hex
    evt_id_2 = uuid.uuid4().hex

    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src.state.events.append(_msg(evt_id_1, "hello"))
        src.state.events.append(_msg(evt_id_2, "world"))

        fork = src.fork()
        fork_id = fork.id

        # The fork should have the events in-memory
        assert len(fork.state.events) == 2

        # Close the fork to flush persistence, then reopen from disk
        fork.close()

        resumed = Conversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            conversation_id=fork_id,
        )
        resumed_ids = [e.id for e in resumed.state.events]
        assert evt_id_1 in resumed_ids
        assert evt_id_2 in resumed_ids


def test_fork_default_does_not_clobber_source_cache_key():
    """Default fork() must leave the source's prompt_cache_key intact (#2917)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src_key_before = src.get_llm_call_context().prompt_cache_key

        fork = src.fork()

        assert (
            src.get_llm_call_context().prompt_cache_key == src_key_before == str(src.id)
        )
        assert fork.get_llm_call_context().prompt_cache_key == str(fork.id)
        assert (
            fork.get_llm_call_context().prompt_cache_key
            != src.get_llm_call_context().prompt_cache_key
        )


def test_fork_with_aliased_agent_does_not_clobber_source_cache_key():
    """fork(agent=source.agent) must preserve source cache identity (#2917)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src_key_before = src.get_llm_call_context().prompt_cache_key

        fork = src.fork(agent=src.agent)

        assert (
            src.get_llm_call_context().prompt_cache_key == src_key_before == str(src.id)
        )
        assert fork.get_llm_call_context().prompt_cache_key == str(fork.id)
        assert fork.agent.llm is not src.agent.llm


def _persisted_base_state(conv: LocalConversation) -> dict:
    persistence_dir = conv.state.persistence_dir
    assert persistence_dir is not None
    return json.loads((Path(persistence_dir) / "base_state.json").read_text())


def test_fork_keeps_source_cipher_for_persisted_secrets(tmp_path):
    """A fork of a cipher-backed conversation stores its secrets encrypted (#5465).

    The agent-server resumes a fork from its ``base_state.json``, so a key
    persisted redacted there makes the fork's first LLM call fail.
    """
    cipher = Cipher("test-secret-key")
    src = LocalConversation(
        agent=_agent(),
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "conversations"),
        cipher=cipher,
        secrets={"GITHUB_TOKEN": "ghp-test"},
    )
    fork = src.fork()

    state = _persisted_base_state(fork)
    api_key = cipher.decrypt(state["agent"]["llm"]["api_key"])
    assert api_key is not None
    assert api_key.get_secret_value() == "test-key"

    # Conversation secrets survive the fork too, under the same cipher.
    sources = state["secret_registry"]["secret_sources"]
    token = cipher.decrypt(sources["GITHUB_TOKEN"]["value"])
    assert token is not None
    assert token.get_secret_value() == "ghp-test"

    # Resuming the fork from disk (the agent-server path) restores the key.
    resumed = ConversationState.model_validate(state, context={"cipher": cipher})
    resumed_key = resumed.agent.llm.api_key
    assert isinstance(resumed_key, SecretStr)
    assert resumed_key.get_secret_value() == "test-key"


def test_fork_without_cipher_still_redacts_secrets(tmp_path):
    """Forks of cipher-less conversations keep redacting persisted secrets."""
    src = LocalConversation(
        agent=_agent(),
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "conversations"),
    )
    fork = src.fork()

    assert set(_persisted_base_state(fork)["agent"]["llm"]["api_key"]) == {"*"}


def test_fork_keeps_confirmation_policy(tmp_path):
    """A fork asks for confirmation under the same policy as its source.

    The fork's base_state.json must carry it too: the agent-server resumes a
    fork from that file.
    """
    policy = ConfirmRisky(threshold=SecurityRisk.MEDIUM, confirm_unknown=False)
    src = LocalConversation(
        agent=_agent(),
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "conversations"),
    )
    src.set_confirmation_policy(policy)

    fork = src.fork()

    assert fork.state.confirmation_policy == policy
    assert _persisted_base_state(fork)["confirmation_policy"] == {
        "kind": "ConfirmRisky",
        "threshold": "MEDIUM",
        "confirm_unknown": False,
    }


def test_fork_copies_secret_registry():
    """Registry-direct secrets must survive a fork.

    base_state.json is the fork's only source of truth for secrets, so fork()
    must copy the source's secret_registry into the fork's state; otherwise a
    token injected via ``Conversation(secrets=...)`` is empty on the fork.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            secrets={"MY_TOKEN": "s3kr3t-value"},
        )

        fork = src.fork()

        assert fork.state.secret_registry.get_secret_value("MY_TOKEN") == (
            "s3kr3t-value"
        )


def test_fork_secret_change_does_not_reach_the_source_masking():
    """The fork resolves and masks with a registry of its own: replacing a
    secret on the fork leaves the source masking its own value."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            secrets={"TOKEN": "source-token-value"},
        )
        src_registry = src.state.secret_registry
        assert src_registry.get_secrets_as_env_vars("echo $TOKEN") == {
            "TOKEN": "source-token-value"
        }

        fork = src.fork()
        fork.update_secrets({"TOKEN": "fork-token-value"})
        assert fork.state.secret_registry.get_secrets_as_env_vars("echo $TOKEN") == {
            "TOKEN": "fork-token-value"
        }

        assert (
            src.state.secret_registry.mask_secrets_in_output("out: source-token-value")
            == "out: <secret-hidden>"
        )


def test_fork_keeps_a_replacement_agents_own_secrets():
    """`fork(agent=...)` with an agent that brings secrets in its context has
    them next to the ones copied from the source."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            secrets={"SOURCE_TOKEN": "source-value"},
        )
        replacement = Agent(
            llm=LLM(model="gpt-4o-mini", api_key=SecretStr("k"), usage_id="other"),
            tools=[],
            agent_context=AgentContext(secrets={"AGENT_TOKEN": "agent-value"}),
        )

        fork = src.fork(agent=replacement)

        registry = fork.state.secret_registry
        assert registry.get_secret_value("AGENT_TOKEN") == "agent-value"
        assert registry.get_secret_value("SOURCE_TOKEN") == "source-value"


def test_fork_gives_independent_security_analyzer():
    """The fork's analyzer is a copy of its own, down to the guardrail LLM: a
    shared analyzer, or a shallow copy of one, would mix the two conversations
    in a stateful analyzer such as ToolShieldLLMSecurityAnalyzer."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src.set_security_analyzer(
            ToolShieldLLMSecurityAnalyzer(
                llm=LLM(model="gpt-4o-mini", usage_id="guard"), history_window=7
            )
        )

        fork = src.fork()

        source_analyzer = src.state.security_analyzer
        fork_analyzer = fork.state.security_analyzer
        assert isinstance(source_analyzer, ToolShieldLLMSecurityAnalyzer)
        assert isinstance(fork_analyzer, ToolShieldLLMSecurityAnalyzer)
        assert fork_analyzer is not source_analyzer
        assert fork_analyzer.llm is not source_analyzer.llm
        assert fork_analyzer.history_window == 7
        assert fork_analyzer.llm.model == "gpt-4o-mini"


def test_fork_copies_a_secret_source_that_cannot_be_deep_copied():
    """A custom secret source may hold a lock or a client; the fork still gets
    a working copy of it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            secrets={"LOCKED": _LockedSecretSource()},
        )

        fork = src.fork()

        assert fork.state.secret_registry.get_secret_value("LOCKED") == (
            "locked-source-value"
        )
        fork_source = fork.state.secret_registry.secret_sources["LOCKED"]
        assert fork_source is not src.state.secret_registry.secret_sources["LOCKED"]


def test_fork_copies_invoked_skills():
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        src._state.invoked_skills = ["release-notes"]

        fork = src.fork()

        assert fork.state.invoked_skills == ["release-notes"]


def test_fork_preserves_ciphered_security_analyzer_credentials():
    """A fork persisted with the parent's cipher must keep the analyzer's
    nested guardrail-LLM api_key instead of redacting it to base_state.json.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cipher = Cipher("stable-conversation-key")
        src = LocalConversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            cipher=cipher,
        )
        src.set_security_analyzer(
            ToolShieldLLMSecurityAnalyzer(
                llm=LLM(
                    model="gpt-4o-mini",
                    api_key=SecretStr("sk-GUARD-xyz"),
                    usage_id="guard",
                )
            )
        )

        fork = src.fork()

        # Resume the fork from its base_state.json with the cipher, the same way
        # the agent-server fork path reloads it.
        assert fork.state.persistence_dir is not None
        base = str(Path(fork.state.persistence_dir).parent)
        resumed = LocalConversation(
            agent=_agent(),
            workspace=tmpdir,
            persistence_dir=base,
            conversation_id=fork.id,
            cipher=cipher,
        )

        resumed_analyzer = resumed.state.security_analyzer
        assert isinstance(resumed_analyzer, ToolShieldLLMSecurityAnalyzer)
        assert isinstance(resumed_analyzer.llm.api_key, SecretStr)
        assert resumed_analyzer.llm.api_key.get_secret_value() == "sk-GUARD-xyz"


def test_fork_preserves_max_budget_per_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(
            agent=_agent(),
            persistence_dir=tmpdir,
            workspace=tmpdir,
            max_budget_per_run=2.0,
        )
        fork = src.fork()

        assert isinstance(fork, LocalConversation)
        assert fork.max_budget_per_run == 2.0
