"""Tests for SDK-1: per-run cost and input-token budgets."""

import tempfile

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_registry import RegistryEvent


def _make_agent() -> Agent:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


def _register_llm_for_metrics(conv: Conversation, llm: LLM) -> None:
    """Register an LLM with the conversation stats so we can mutate metrics."""
    conv._state.stats.register_llm(RegistryEvent(llm=llm))


# -- Construction & validation ----------------------------------------------


def test_conversation_accepts_run_budgets():
    agent = _make_agent()
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent,
            persistence_dir=tmpdir,
            workspace=tmpdir,
            max_cost_per_run=10.0,
            max_input_tokens_per_run=100_000,
        )
    assert conv.max_cost_per_run == 10.0
    assert conv.max_input_tokens_per_run == 100_000


def test_default_run_budgets_are_none():
    agent = _make_agent()
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)
    assert conv.max_cost_per_run is None
    assert conv.max_input_tokens_per_run is None


@pytest.mark.parametrize(
    "kwarg",
    [
        {"max_cost_per_run": 0},
        {"max_cost_per_run": -1.5},
        {"max_input_tokens_per_run": 0},
        {"max_input_tokens_per_run": -1},
    ],
)
def test_non_positive_run_budgets_rejected(kwarg):
    agent = _make_agent()
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError):
            Conversation(
                agent=agent,
                persistence_dir=tmpdir,
                workspace=tmpdir,
                **kwarg,
            )


# -- _check_run_budgets() semantics -----------------------------------------


def test_check_run_budgets_returns_none_when_disabled():
    agent = _make_agent()
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)
        _register_llm_for_metrics(conv, agent.llm)
        assert agent.llm.metrics is not None
        agent.llm.metrics.add_cost(999.0)
        assert conv._check_run_budgets() is None


def test_check_run_budgets_flags_cost_breach():
    agent = _make_agent()
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent,
            persistence_dir=tmpdir,
            workspace=tmpdir,
            max_cost_per_run=1.0,
        )
        _register_llm_for_metrics(conv, agent.llm)
        assert agent.llm.metrics is not None

        # Under budget → no breach.
        agent.llm.metrics.add_cost(0.5)
        assert conv._check_run_budgets() is None

        # At/over budget → breach with code + readable message.
        agent.llm.metrics.add_cost(0.6)
        hit = conv._check_run_budgets()
        assert hit is not None
        code, msg = hit
        assert code == "MaxCostExceeded"
        assert "1.10" in msg and "1.00" in msg


def test_check_run_budgets_flags_input_token_breach():
    agent = _make_agent()
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent,
            persistence_dir=tmpdir,
            workspace=tmpdir,
            max_input_tokens_per_run=1_000,
        )
        _register_llm_for_metrics(conv, agent.llm)
        assert agent.llm.metrics is not None

        agent.llm.metrics.add_token_usage(
            prompt_tokens=999,
            completion_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_window=128_000,
            response_id="r1",
        )
        assert conv._check_run_budgets() is None

        agent.llm.metrics.add_token_usage(
            prompt_tokens=2,
            completion_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_window=128_000,
            response_id="r2",
        )
        hit = conv._check_run_budgets()
        assert hit is not None
        code, msg = hit
        assert code == "MaxInputTokensExceeded"
        assert "1001" in msg and "1000" in msg


def test_cost_breach_takes_precedence_over_token_breach():
    """When both limits are exceeded, the cost message wins (it's the
    higher-signal one for eval harnesses)."""
    agent = _make_agent()
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent,
            persistence_dir=tmpdir,
            workspace=tmpdir,
            max_cost_per_run=0.10,
            max_input_tokens_per_run=10,
        )
        _register_llm_for_metrics(conv, agent.llm)
        assert agent.llm.metrics is not None
        agent.llm.metrics.add_cost(0.50)
        agent.llm.metrics.add_token_usage(
            prompt_tokens=100,
            completion_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_window=128_000,
            response_id="r",
        )
        hit = conv._check_run_budgets()
        assert hit is not None
        assert hit[0] == "MaxCostExceeded"


# -- _emit_run_budget_exceeded() end-to-end ---------------------------------


def test_emit_run_budget_exceeded_sets_error_and_emits_event():
    agent = _make_agent()
    seen: list[ConversationErrorEvent] = []

    def cb(event):
        if isinstance(event, ConversationErrorEvent):
            seen.append(event)

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent,
            persistence_dir=tmpdir,
            workspace=tmpdir,
            max_cost_per_run=1.0,
            callbacks=[cb],
        )
        conv._emit_run_budget_exceeded("MaxCostExceeded", "over budget")

    assert conv._state.execution_status == ConversationExecutionStatus.ERROR
    assert len(seen) == 1
    assert seen[0].code == "MaxCostExceeded"
    assert seen[0].detail == "over budget"


def test_emit_run_budget_exceeded_preserves_finished_status():
    """If the agent already finished on this iteration, don't overwrite
    FINISHED with ERROR (mirrors the existing MaxIterationsReached path)."""
    agent = _make_agent()
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent,
            persistence_dir=tmpdir,
            workspace=tmpdir,
            max_cost_per_run=1.0,
        )
        conv._state.execution_status = ConversationExecutionStatus.FINISHED
        conv._emit_run_budget_exceeded("MaxCostExceeded", "over budget")

    # Status preserved as FINISHED, no event emitted.
    assert conv._state.execution_status == ConversationExecutionStatus.FINISHED


# -- Remote workspace guardrail ---------------------------------------------


def test_remote_conversation_rejects_run_budgets():
    """RemoteConversation cannot enforce caps until the agent server picks
    them up — make sure misuse is loud, not silent."""
    from openhands.sdk.workspace import RemoteWorkspace

    agent = _make_agent()
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/workspace")

    with pytest.raises(NotImplementedError):
        Conversation(
            agent=agent,
            workspace=workspace,
            max_cost_per_run=1.0,
        )

    with pytest.raises(NotImplementedError):
        Conversation(
            agent=agent,
            workspace=workspace,
            max_input_tokens_per_run=1000,
        )
