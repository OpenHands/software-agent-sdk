"""Stress tests for delegation in the agent-server context.

These tests verify that the DelegateTool works correctly under concurrent load
by spawning ~10 sub-agents and delegating tasks in parallel.  Sub-agent
``send_message`` / ``run`` calls are mocked, and ``get_agent_final_response``
is patched so no real LLM calls are made.
"""

import threading
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM
from openhands.sdk.subagent.registry import _reset_registry_for_tests
from openhands.tools.delegate import DelegateExecutor
from openhands.tools.delegate.definition import DelegateAction
from openhands.tools.preset import register_builtins_agents


NUM_AGENTS = 10

# Module path used by _delegate_tasks to extract final responses.
_RESPONSE_UTILS = "openhands.tools.delegate.impl.get_agent_final_response"


def _make_parent_conversation():
    """Create a mock parent conversation for delegation tests."""
    llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        base_url="https://api.openai.com/v1",
    )
    parent_stats = ConversationStats()
    parent_stats.usage_to_metrics["agent"] = llm.metrics

    parent = MagicMock()
    parent.id = uuid.uuid4()
    parent.agent.llm = llm
    parent.state.workspace.working_dir = "/tmp"
    parent.state.persistence_dir = None
    parent._visualizer = None
    parent.conversation_stats = parent_stats
    return parent


def _patch_sub_agents(executor, ids, *, fail_ids=None):
    """Patch ``send_message`` and ``run`` on each spawned sub-agent.

    For agents in *fail_ids*, ``send_message`` raises ``RuntimeError``.
    """
    fail_ids = fail_ids or set()
    for agent_id in ids:
        sub = executor._sub_agents[agent_id]
        if agent_id in fail_ids:
            sub.send_message = MagicMock(side_effect=RuntimeError(f"boom-{agent_id}"))
        else:
            sub.send_message = MagicMock()
        sub.run = MagicMock()
        sub.state._execution_status = ConversationExecutionStatus.FINISHED


def _fake_response_factory(ids):
    """Return a side_effect callable for ``get_agent_final_response``.

    Maps events belonging to each sub-agent to a deterministic string.
    Since all sub-agents share the same patched mock, we simply rotate
    through the id list on successive calls.
    """
    call_count = {"n": 0}

    def _fake(events):  # noqa: ARG001
        idx = call_count["n"] % len(ids)
        call_count["n"] += 1
        return f"Done: result from {ids[idx]}"

    return _fake


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the sub-agent registry before each test."""
    _reset_registry_for_tests()
    register_builtins_agents()
    yield
    _reset_registry_for_tests()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDelegationStress:
    """Suite of stress tests exercising ~10 concurrent delegations."""

    def test_spawn_10_agents(self):
        """Spawn 10 sub-agents in a single call and verify all are created."""
        parent = _make_parent_conversation()
        executor = DelegateExecutor(max_children=NUM_AGENTS + 5)

        ids = [f"worker_{i}" for i in range(NUM_AGENTS)]
        obs = executor(DelegateAction(command="spawn", ids=ids), parent)

        assert not obs.is_error
        assert f"Successfully spawned {NUM_AGENTS}" in obs.text
        assert len(executor._sub_agents) == NUM_AGENTS
        for agent_id in ids:
            assert agent_id in executor._sub_agents

    def test_delegate_10_tasks_concurrently(self):
        """Spawn 10 agents, delegate one task each, verify parallel completion."""
        parent = _make_parent_conversation()
        executor = DelegateExecutor(max_children=NUM_AGENTS + 5)

        ids = [f"worker_{i}" for i in range(NUM_AGENTS)]
        executor(DelegateAction(command="spawn", ids=ids), parent)
        _patch_sub_agents(executor, ids)

        tasks = {aid: f"Task for {aid}" for aid in ids}
        with patch(_RESPONSE_UTILS, side_effect=_fake_response_factory(ids)):
            obs = executor(DelegateAction(command="delegate", tasks=tasks), parent)

        assert not obs.is_error
        assert f"Completed delegation of {NUM_AGENTS} tasks" in obs.text

    def test_delegate_10_with_simulated_latency(self):
        """Each sub-agent sleeps briefly to simulate LLM latency; all run in
        parallel so total wall-clock time stays well below serial sum."""
        parent = _make_parent_conversation()
        executor = DelegateExecutor(max_children=NUM_AGENTS + 5)

        ids = [f"latency_{i}" for i in range(NUM_AGENTS)]
        executor(DelegateAction(command="spawn", ids=ids), parent)

        sleep_per_agent = 0.15  # seconds

        for agent_id in ids:
            sub = executor._sub_agents[agent_id]
            sub.send_message = MagicMock()
            sub.run = MagicMock(side_effect=lambda: time.sleep(sleep_per_agent))
            sub.state._execution_status = ConversationExecutionStatus.FINISHED

        tasks = {aid: f"Slow task {aid}" for aid in ids}
        with patch(_RESPONSE_UTILS, return_value="done"):
            start = time.monotonic()
            obs = executor(DelegateAction(command="delegate", tasks=tasks), parent)
            elapsed = time.monotonic() - start

        assert not obs.is_error
        assert f"Completed delegation of {NUM_AGENTS} tasks" in obs.text
        # If truly parallel, elapsed should be much less than serial total.
        serial_total = sleep_per_agent * NUM_AGENTS
        assert elapsed < serial_total * 0.6, (
            f"Delegation took {elapsed:.2f}s, expected < {serial_total * 0.6:.2f}s "
            f"(serial would be {serial_total:.2f}s)"
        )

    def test_delegate_10_mixed_success_and_failure(self):
        """Half the agents succeed, half raise; delegation still completes."""
        parent = _make_parent_conversation()
        executor = DelegateExecutor(max_children=NUM_AGENTS + 5)

        ids = [f"mixed_{i}" for i in range(NUM_AGENTS)]
        executor(DelegateAction(command="spawn", ids=ids), parent)

        fail_ids = {ids[i] for i in range(NUM_AGENTS) if i % 2 != 0}
        _patch_sub_agents(executor, ids, fail_ids=fail_ids)

        tasks = {aid: f"Task {aid}" for aid in ids}
        with patch(_RESPONSE_UTILS, return_value="ok"):
            obs = executor(DelegateAction(command="delegate", tasks=tasks), parent)

        # Should complete without raising, reporting errors in output
        assert f"Completed delegation of {NUM_AGENTS} tasks" in obs.text
        assert f"{NUM_AGENTS // 2} error" in obs.text.lower()

    def test_delegate_10_metrics_merged(self):
        """Verify that metrics from all 10 sub-agents are merged into the parent."""
        parent = _make_parent_conversation()
        executor = DelegateExecutor(max_children=NUM_AGENTS + 5)

        ids = [f"metric_{i}" for i in range(NUM_AGENTS)]
        executor(DelegateAction(command="spawn", ids=ids), parent)
        _patch_sub_agents(executor, ids)

        for i, agent_id in enumerate(ids):
            sub = executor._sub_agents[agent_id]
            # Wire sub-agent LLM metrics into sub conversation stats
            sub.conversation_stats.usage_to_metrics[sub.agent.llm.usage_id] = (
                sub.agent.llm.metrics
            )
            sub.agent.llm.metrics.add_cost(float(i + 1))

        tasks = {aid: f"Task {aid}" for aid in ids}
        with patch(_RESPONSE_UTILS, return_value="ok"):
            obs = executor(DelegateAction(command="delegate", tasks=tasks), parent)

        assert not obs.is_error
        for agent_id in ids:
            assert f"delegate:{agent_id}" in parent.conversation_stats.usage_to_metrics

        # Total cost across all delegates: 1+2+...+10 = 55
        combined = parent.conversation_stats.get_combined_metrics()
        assert combined.accumulated_cost == pytest.approx(55.0)

    def test_delegate_10_threads_are_independent(self):
        """Each sub-agent runs on its own thread; verify thread names."""
        parent = _make_parent_conversation()
        executor = DelegateExecutor(max_children=NUM_AGENTS + 5)

        ids = [f"thread_{i}" for i in range(NUM_AGENTS)]
        executor(DelegateAction(command="spawn", ids=ids), parent)

        seen_threads: dict[str, str] = {}
        lock = threading.Lock()

        for agent_id in ids:
            sub = executor._sub_agents[agent_id]
            sub.send_message = MagicMock()

            def _capture_thread(aid=agent_id):
                with lock:
                    seen_threads[aid] = threading.current_thread().name

            sub.run = MagicMock(side_effect=_capture_thread)
            sub.state._execution_status = ConversationExecutionStatus.FINISHED

        tasks = {aid: f"Task {aid}" for aid in ids}
        with patch(_RESPONSE_UTILS, return_value="ok"):
            executor(DelegateAction(command="delegate", tasks=tasks), parent)

        assert len(seen_threads) == NUM_AGENTS
        thread_names = set(seen_threads.values())
        assert len(thread_names) == NUM_AGENTS, (
            f"Expected {NUM_AGENTS} unique threads, got {len(thread_names)}"
        )

    def test_spawn_exceeding_max_children(self):
        """Spawning more than max_children is rejected."""
        parent = _make_parent_conversation()
        executor = DelegateExecutor(max_children=5)

        ids = [f"over_{i}" for i in range(NUM_AGENTS)]
        obs = executor(DelegateAction(command="spawn", ids=ids), parent)

        assert obs.is_error
        assert "Cannot spawn" in obs.text
        assert len(executor._sub_agents) == 0

    def test_delegate_to_nonexistent_agent(self):
        """Delegating to unknown agent IDs is rejected."""
        parent = _make_parent_conversation()
        executor = DelegateExecutor(max_children=NUM_AGENTS + 5)

        ids = [f"exists_{i}" for i in range(5)]
        executor(DelegateAction(command="spawn", ids=ids), parent)

        tasks = {f"ghost_{i}": f"Task {i}" for i in range(NUM_AGENTS)}
        obs = executor(DelegateAction(command="delegate", tasks=tasks), parent)

        assert obs.is_error
        assert "not found" in obs.text

    def test_repeated_delegation_10_rounds(self):
        """Delegate 10 sequential rounds to the same set of agents without
        double-counting metrics."""
        parent = _make_parent_conversation()
        executor = DelegateExecutor(max_children=NUM_AGENTS + 5)

        ids = [f"repeat_{i}" for i in range(NUM_AGENTS)]
        executor(DelegateAction(command="spawn", ids=ids), parent)
        _patch_sub_agents(executor, ids)

        for agent_id in ids:
            sub = executor._sub_agents[agent_id]
            sub.conversation_stats.usage_to_metrics[sub.agent.llm.usage_id] = (
                sub.agent.llm.metrics
            )

        with patch(_RESPONSE_UTILS, return_value="ok"):
            for round_num in range(10):
                for agent_id in ids:
                    sub = executor._sub_agents[agent_id]
                    sub.agent.llm.metrics.add_cost(1.0)

                tasks = {aid: f"Round {round_num} task" for aid in ids}
                obs = executor(DelegateAction(command="delegate", tasks=tasks), parent)
                assert not obs.is_error

        # Each agent: $1 * 10 rounds = $10; total = $10 * 10 agents = $100
        combined = parent.conversation_stats.get_combined_metrics()
        assert combined.accumulated_cost == pytest.approx(100.0)

    def test_delegate_10_with_typed_agents(self):
        """Spawn 10 agents with explicit types (bash, explore, default)."""
        parent = _make_parent_conversation()
        executor = DelegateExecutor(max_children=NUM_AGENTS + 5)

        ids = [f"typed_{i}" for i in range(NUM_AGENTS)]
        agent_types = (["bash", "explore", "default"] * 4)[:NUM_AGENTS]

        obs = executor(
            DelegateAction(command="spawn", ids=ids, agent_types=agent_types),
            parent,
        )
        assert not obs.is_error
        assert f"Successfully spawned {NUM_AGENTS}" in obs.text
        for agent_id in ids:
            assert agent_id in executor._sub_agents

        _patch_sub_agents(executor, ids)
        tasks = {aid: f"Typed task {aid}" for aid in ids}
        with patch(_RESPONSE_UTILS, return_value="ok"):
            obs = executor(DelegateAction(command="delegate", tasks=tasks), parent)

        assert not obs.is_error
        assert f"Completed delegation of {NUM_AGENTS} tasks" in obs.text
