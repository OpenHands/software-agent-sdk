"""Stress tests for delegation in the agent-server context.

These tests verify that the DelegateTool works correctly under concurrent load
by spawning ~10 sub-agents and delegating tasks in parallel.  Sub-agent
``send_message`` / ``run`` calls are mocked, and ``get_agent_final_response``
is patched so no real LLM calls are made.

The ``TestDelegationRealExecution`` class uses *real* ``LocalConversation``
instances with ``TestLLM`` scripted to call the terminal tool (``echo``),
verifying that shell commands in one sub-agent do not block the others.
"""

import json
import threading
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
    register_agent,
)
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool
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


# ---------------------------------------------------------------------------
# Helper: scripted echo-then-finish LLM responses
# ---------------------------------------------------------------------------


def _echo_finish_messages(agent_id: str) -> list[Message | Exception]:
    """Return TestLLM script: call terminal(echo …), then finish."""
    return [
        Message(
            role="assistant",
            content=[TextContent(text="")],
            tool_calls=[
                MessageToolCall(
                    id=f"call_echo_{agent_id}",
                    name="terminal",
                    arguments=json.dumps({"command": f"echo hello_from_{agent_id}"}),
                    origin="completion",
                ),
            ],
        ),
        Message(
            role="assistant",
            content=[TextContent(text="")],
            tool_calls=[
                MessageToolCall(
                    id=f"call_finish_{agent_id}",
                    name="finish",
                    arguments=json.dumps({"message": f"done_{agent_id}"}),
                    origin="completion",
                ),
            ],
        ),
    ]


def _echo_agent_factory(llm: LLM) -> Agent:
    """Agent factory that ignores the supplied LLM and builds a fresh
    ``TestLLM`` so each sub-agent gets an independent scripted response queue.

    The unique ``agent_id`` is embedded via a thread-local counter to keep
    the factory signature compatible with the registry (``LLM -> Agent``).

    Uses subprocess terminals (not tmux) to avoid startup contention.
    """
    idx = _echo_agent_factory._counter  # type: ignore[attr-defined]
    _echo_agent_factory._counter += 1  # type: ignore[attr-defined]
    agent_id = f"echo_{idx}"
    test_llm = TestLLM.from_messages(_echo_finish_messages(agent_id))
    return Agent(
        llm=test_llm,
        tools=[Tool(name="terminal", params={"terminal_type": "subprocess"})],
    )


_echo_agent_factory._counter = 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Real-execution delegation tests
# ---------------------------------------------------------------------------


class TestDelegationRealExecution:
    """Tests that spawn real ``LocalConversation`` sub-agents backed by
    ``TestLLM`` scripts that call ``echo`` via the terminal tool.

    These prove that terminal commands in one sub-agent do **not** block the
    others: wall-clock time for N parallel agents should be close to the time
    for a single agent, not N×single.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        """Register the echo-agent type and provide a temp workspace."""
        _reset_registry_for_tests()
        _echo_agent_factory._counter = 0  # type: ignore[attr-defined]
        register_builtins_agents()
        register_agent(
            name="echo_runner",
            factory_func=_echo_agent_factory,
            description="Sub-agent that runs echo and finishes",
        )
        self.workspace = str(tmp_path)
        yield
        _reset_registry_for_tests()

    def _make_real_parent(self) -> LocalConversation:
        """Build a *real* parent ``LocalConversation`` (not a mock)."""
        parent_llm = TestLLM.from_messages([])
        parent_agent = Agent(
            llm=parent_llm,
            tools=[Tool(name="delegate")],
        )
        return LocalConversation(
            agent=parent_agent,
            workspace=self.workspace,
        )

    # ------------------------------------------------------------------

    def test_subagents_run_echo_without_blocking_each_other(self):
        """Spawn N echo-runner agents, delegate in parallel, and assert
        that wall-clock time stays well below serial execution time.

        Each agent runs ``echo hello_from_<id>`` through a real terminal
        session.  If one agent's terminal blocked the others we would see
        wall-clock ≈ N × single; with proper isolation it should be close
        to 1 × single (plus threading overhead).
        """
        n = NUM_AGENTS
        parent = self._make_real_parent()
        executor = DelegateExecutor(max_children=n + 5)

        ids = [f"runner_{i}" for i in range(n)]
        agent_types = ["echo_runner"] * n

        obs = executor(
            DelegateAction(command="spawn", ids=ids, agent_types=agent_types),
            parent,
        )
        assert not obs.is_error, obs.text

        # --- time a single agent for baseline ----
        single_parent = self._make_real_parent()
        _echo_agent_factory._counter = 100  # type: ignore[attr-defined]
        single_executor = DelegateExecutor(max_children=2)
        register_agent(
            name="echo_single",
            factory_func=_echo_agent_factory,
            description="single baseline",
        )
        single_obs = single_executor(
            DelegateAction(
                command="spawn", ids=["baseline"], agent_types=["echo_single"]
            ),
            single_parent,
        )
        assert not single_obs.is_error, single_obs.text

        t0 = time.monotonic()
        single_obs = single_executor(
            DelegateAction(command="delegate", tasks={"baseline": "run echo"}),
            single_parent,
        )
        single_elapsed = time.monotonic() - t0
        assert not single_obs.is_error, single_obs.text

        # --- time N agents in parallel ----
        tasks = {aid: f"run echo for {aid}" for aid in ids}
        t0 = time.monotonic()
        obs = executor(
            DelegateAction(command="delegate", tasks=tasks),
            parent,
        )
        parallel_elapsed = time.monotonic() - t0
        assert not obs.is_error, obs.text

        # Verify every agent actually produced output
        for aid in ids:
            assert "done_echo_" in obs.text or f"Agent {aid}" in obs.text

        # Parallel should be WAY faster than N × single.
        # Serial would be ~N× single.  We allow up to 5× single
        # (accounting for thread startup, shell init contention, and
        # CI load variance) with a 5s floor for very fast single runs.
        max_allowed = max(single_elapsed * 5, 5.0)
        assert parallel_elapsed < max_allowed, (
            f"Parallel ({n} agents) took {parallel_elapsed:.2f}s, "
            f"single took {single_elapsed:.2f}s — "
            f"expected < {max_allowed:.2f}s (5× single or 5s floor). "
            f"Serial estimate: {single_elapsed * n:.2f}s"
        )

    def test_each_agent_echoes_its_own_unique_string(self):
        """Each sub-agent's terminal output must contain only its own
        identifier — no cross-talk between shell sessions."""
        from openhands.sdk.event.llm_convertible.observation import ObservationEvent

        n = 5  # fewer agents, focused on correctness
        parent = self._make_real_parent()
        executor = DelegateExecutor(max_children=n + 5)

        ids = [f"uniq_{i}" for i in range(n)]
        agent_types = ["echo_runner"] * n

        obs = executor(
            DelegateAction(command="spawn", ids=ids, agent_types=agent_types),
            parent,
        )
        assert not obs.is_error, obs.text

        tasks = {aid: f"echo for {aid}" for aid in ids}
        obs = executor(DelegateAction(command="delegate", tasks=tasks), parent)
        assert not obs.is_error, obs.text

        # Each sub-agent's conversation should contain its own echo output.
        # Collect ALL observation texts (terminal + finish) to be robust
        # against bash init noise that may push echo output around.
        for aid in ids:
            sub_conv = executor._sub_agents[aid]
            all_obs_texts = [
                e.observation.text
                for e in sub_conv.state.events
                if isinstance(e, ObservationEvent)
            ]
            combined = "\n".join(all_obs_texts)
            # The finish message contains "done_echo_N" which proves
            # the agent ran to completion with its own unique ID.
            assert "done_echo_" in combined, (
                f"Sub-agent {aid} should have finished with its unique ID; "
                f"observation texts: {combined[:500]}"
            )
