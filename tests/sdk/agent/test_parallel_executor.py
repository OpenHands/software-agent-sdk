"""Tests for ParallelToolExecutor."""

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from openhands.sdk.agent.parallel_executor import (
    DEFAULT_TOOL_CONCURRENCY_LIMIT,
    ENV_TOOL_CONCURRENCY_LIMIT,
    ParallelToolExecutor,
    _get_max_concurrency,
)
from openhands.sdk.event.llm_convertible import AgentErrorEvent


def test_get_max_concurrency_default():
    assert _get_max_concurrency() == DEFAULT_TOOL_CONCURRENCY_LIMIT


@pytest.mark.parametrize(
    "env_value, expected",
    [
        ("4", 4),
        ("1", 1),
        ("not_a_number", DEFAULT_TOOL_CONCURRENCY_LIMIT),
        ("0", DEFAULT_TOOL_CONCURRENCY_LIMIT),
        ("-1", DEFAULT_TOOL_CONCURRENCY_LIMIT),
    ],
)
def test_get_max_concurrency_from_env(monkeypatch, env_value, expected):
    monkeypatch.setenv(ENV_TOOL_CONCURRENCY_LIMIT, env_value)
    assert _get_max_concurrency() == expected


def test_empty_batch():
    executor = ParallelToolExecutor()
    results = executor.execute_batch([], lambda x: [MagicMock()])
    assert results == []


def test_single_action_bypasses_thread_pool():
    executor = ParallelToolExecutor()
    action: Any = MagicMock()
    event = MagicMock()

    results = executor.execute_batch([action], lambda a: [event])
    assert len(results) == 1
    assert results[0] == [event]


def test_result_ordering_preserved_despite_variable_duration():
    """Results are in input order even when later actions finish first."""
    executor = ParallelToolExecutor()
    actions: list[Any] = [MagicMock() for _ in range(5)]

    def tool_runner(action: Any) -> list:
        idx = actions.index(action)
        time.sleep((5 - idx) * 0.01)  # First action sleeps longest
        return [f"result-{idx}"]

    results = executor.execute_batch(actions, tool_runner)

    assert results == [
        ["result-0"],
        ["result-1"],
        ["result-2"],
        ["result-3"],
        ["result-4"],
    ]


def test_actions_run_concurrently(monkeypatch):
    """Verify that actions actually run in parallel, not sequentially."""
    monkeypatch.setenv(ENV_TOOL_CONCURRENCY_LIMIT, "4")
    executor = ParallelToolExecutor()
    actions: list[Any] = [MagicMock() for _ in range(4)]
    max_concurrent = [0]
    current = [0]
    lock = threading.Lock()

    def tool_runner(action: Any) -> list:
        with lock:
            current[0] += 1
            max_concurrent[0] = max(max_concurrent[0], current[0])
        time.sleep(0.05)
        with lock:
            current[0] -= 1
        return [MagicMock()]

    executor.execute_batch(actions, tool_runner)

    assert max_concurrent[0] > 1


def test_concurrency_limited_by_max_workers(monkeypatch):
    """Concurrency does not exceed the configured limit."""
    monkeypatch.setenv(ENV_TOOL_CONCURRENCY_LIMIT, "2")

    executor = ParallelToolExecutor()
    actions: list[Any] = [MagicMock() for _ in range(6)]
    concurrent_count: list[int] = []
    lock = threading.Lock()
    current = [0]

    def tool_runner(action: Any) -> list:
        with lock:
            current[0] += 1
            concurrent_count.append(current[0])
        time.sleep(0.02)
        with lock:
            current[0] -= 1
        return [MagicMock()]

    executor.execute_batch(actions, tool_runner)

    assert max(concurrent_count) <= 2


def test_multiple_events_per_action():
    """tool_runner can return multiple events for a single action."""
    executor = ParallelToolExecutor()
    actions: list[Any] = [MagicMock(), MagicMock()]

    def tool_runner(action: Any) -> list:
        return [MagicMock(name="obs"), MagicMock(name="followup")]

    results = executor.execute_batch(actions, tool_runner)

    assert len(results) == 2
    assert len(results[0]) == 2
    assert len(results[1]) == 2


def _make_action(name: str = "test_tool", tool_call_id: str = "call_1") -> Any:
    """Create a mock ActionEvent with required fields."""
    action = MagicMock()
    action.tool_name = name
    action.tool_call_id = tool_call_id
    return action


def test_error_returns_agent_error_event_for_single_action():
    """Single action errors are wrapped in AgentErrorEvent."""
    executor = ParallelToolExecutor()
    action = _make_action("my_tool", "call_1")

    def tool_runner(a: Any) -> list:
        raise ValueError("Test error")

    results = executor.execute_batch([action], tool_runner)
    assert len(results) == 1
    assert len(results[0]) == 1
    assert isinstance(results[0][0], AgentErrorEvent)
    assert "Test error" in results[0][0].error


def test_error_returns_agent_error_event_in_batch():
    """
    ValueErrors in a batch produce AgentErrorEvents
    successful results are preserved.
    """
    executor = ParallelToolExecutor()
    actions = [
        _make_action("tool_a", "call_0"),
        _make_action("tool_b", "call_1"),
        _make_action("tool_c", "call_2"),
    ]
    success_event = MagicMock()

    def tool_runner(action: Any) -> list:
        if action.tool_call_id == "call_1":
            raise ValueError("action 1 failed")
        time.sleep(0.02)
        return [success_event]

    results = executor.execute_batch(actions, tool_runner)

    assert len(results) == 3
    assert results[0] == [success_event]
    assert len(results[1]) == 1
    assert isinstance(results[1][0], AgentErrorEvent)
    assert "action 1 failed" in results[1][0].error
    assert results[2] == [success_event]


def test_programming_errors_propagate():
    """Non-ValueError exceptions (bugs) propagate instead of being swallowed."""
    executor = ParallelToolExecutor()
    actions = [
        _make_action("tool_a", "call_0"),
        _make_action("tool_b", "call_1"),
    ]

    def tool_runner(action: Any) -> list:
        if action.tool_call_id == "call_1":
            raise RuntimeError("This should not happen")
        return [MagicMock()]

    with pytest.raises(RuntimeError, match="This should not happen"):
        executor.execute_batch(actions, tool_runner)


def test_nested_execution_no_deadlock():
    """Nested execute_batch (subagent scenario) does not deadlock.

    The outer executor has max_workers=1. The subagent tool creates its
    own executor — since pools are per-instance, no thread starvation.
    """
    outer_executor = ParallelToolExecutor()
    outer_executor._max_workers = 1

    def inner_tool_runner(action: Any) -> list:
        return [f"inner-{action}"]

    def outer_tool_runner(action: Any) -> list:
        if action == "subagent":
            inner_executor = ParallelToolExecutor()
            inner_executor._max_workers = 2
            inner_results = inner_executor.execute_batch(
                ["a", "b"],  # type: ignore[arg-type]
                inner_tool_runner,
            )
            return [item for sublist in inner_results for item in sublist]
        return [f"leaf-{action}"]

    results = outer_executor.execute_batch(
        ["subagent"],  # type: ignore[arg-type]
        outer_tool_runner,
    )

    assert results == [["inner-a", "inner-b"]]
