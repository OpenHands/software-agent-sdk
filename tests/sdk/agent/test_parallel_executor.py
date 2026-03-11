"""Tests for ParallelToolExecutor and ToolExecutorSemaphore."""

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from openhands.sdk.agent.parallel_executor import (
    DEFAULT_TOOL_CONCURRENCY_LIMIT,
    ENV_TOOL_CONCURRENCY_LIMIT,
    ParallelToolExecutor,
    ToolExecutorSemaphore,
)


@pytest.fixture(autouse=True)
def reset_semaphore():
    """Reset the singleton semaphore before and after each test."""
    ToolExecutorSemaphore._instance = None
    ToolExecutorSemaphore._initialized = False
    yield
    ToolExecutorSemaphore._instance = None
    ToolExecutorSemaphore._initialized = False


class TestToolExecutorSemaphore:
    """Tests for ToolExecutorSemaphore."""

    def test_default_concurrency_limit(self):
        """Test that default concurrency limit is applied."""
        semaphore = ToolExecutorSemaphore()
        assert semaphore.max_concurrent == DEFAULT_TOOL_CONCURRENCY_LIMIT

    def test_singleton_pattern(self):
        """Test that instantiation returns the same instance."""
        instance1 = ToolExecutorSemaphore()
        instance2 = ToolExecutorSemaphore()
        assert instance1 is instance2

    def test_env_variable_configuration(self, monkeypatch):
        """Test that env variable overrides default limit."""
        monkeypatch.setenv(ENV_TOOL_CONCURRENCY_LIMIT, "4")
        semaphore = ToolExecutorSemaphore()
        assert semaphore.max_concurrent == 4

    def test_invalid_env_variable_falls_back_to_default(self, monkeypatch):
        """Test that invalid env variable falls back to default."""
        monkeypatch.setenv(ENV_TOOL_CONCURRENCY_LIMIT, "not_a_number")
        semaphore = ToolExecutorSemaphore()
        assert semaphore.max_concurrent == DEFAULT_TOOL_CONCURRENCY_LIMIT

    def test_negative_env_variable_falls_back_to_default(self, monkeypatch):
        """Test that negative env variable falls back to default."""
        monkeypatch.setenv(ENV_TOOL_CONCURRENCY_LIMIT, "-1")
        semaphore = ToolExecutorSemaphore()
        assert semaphore.max_concurrent == DEFAULT_TOOL_CONCURRENCY_LIMIT

    def test_context_manager(self, monkeypatch):
        """Test context manager acquire/release."""
        monkeypatch.setenv(ENV_TOOL_CONCURRENCY_LIMIT, "1")
        semaphore = ToolExecutorSemaphore()
        acquired: list[str] = []

        def worker():
            with semaphore:
                acquired.append(threading.current_thread().name)
                time.sleep(0.1)

        t1 = threading.Thread(target=worker, name="worker-1")
        t2 = threading.Thread(target=worker, name="worker-2")

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both workers should have acquired the semaphore
        assert len(acquired) == 2

    def test_concurrency_limiting(self, monkeypatch):
        """Test that semaphore actually limits concurrency."""
        monkeypatch.setenv(ENV_TOOL_CONCURRENCY_LIMIT, "2")
        semaphore = ToolExecutorSemaphore()
        concurrent_count: list[int] = []
        lock = threading.Lock()
        current_count = [0]

        def worker():
            with semaphore:
                with lock:
                    current_count[0] += 1
                    concurrent_count.append(current_count[0])
                time.sleep(0.05)
                with lock:
                    current_count[0] -= 1

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Maximum concurrent executions should never exceed 2
        assert max(concurrent_count) <= 2


class TestParallelToolExecutor:
    """Tests for ParallelToolExecutor."""

    def test_empty_batch(self):
        """Test handling of empty action list."""
        executor = ParallelToolExecutor()
        results = executor.execute_batch([], lambda x: MagicMock())  # type: ignore[arg-type]
        assert results == []

    def test_single_action_no_thread_pool(self):
        """Test that single action is executed without thread pool."""
        executor = ParallelToolExecutor()
        action: Any = MagicMock()
        result = MagicMock()

        def tool_runner(event: Any) -> Any:
            return result

        results = executor.execute_batch([action], tool_runner)  # type: ignore[arg-type]
        assert len(results) == 1
        assert results[0] is result

    def test_multiple_actions_parallel_execution(self):
        """Test parallel execution of multiple actions."""
        executor = ParallelToolExecutor()
        actions: list[Any] = [MagicMock(name=f"action-{i}") for i in range(4)]
        results_map = {
            id(a): MagicMock(name=f"result-{i}") for i, a in enumerate(actions)
        }

        def tool_runner(action: Any) -> Any:
            time.sleep(0.05)  # Simulate work
            return results_map[id(action)]

        results = executor.execute_batch(actions, tool_runner)  # type: ignore[arg-type]

        # Results should be in original order
        assert len(results) == 4
        for i, action in enumerate(actions):
            assert results[i] is results_map[id(action)]

    def test_result_ordering_preserved(self):
        """Test that results are returned in original order."""
        executor = ParallelToolExecutor()
        actions: list[Any] = [MagicMock() for _ in range(5)]

        # Make each action sleep for a different duration (reverse order)
        def tool_runner(action: Any) -> Any:
            idx = actions.index(action)
            time.sleep((5 - idx) * 0.01)  # First action sleeps longest
            return f"result-{idx}"

        results = executor.execute_batch(actions, tool_runner)  # type: ignore[arg-type]

        # Despite different completion times, results should be in order
        assert results == ["result-0", "result-1", "result-2", "result-3", "result-4"]

    def test_semaphore_limits_concurrency(self, monkeypatch):
        """Test that executor respects the OPENHANDS_TOOL_CONCURRENCY_LIMIT."""
        monkeypatch.setenv(ENV_TOOL_CONCURRENCY_LIMIT, "2")

        executor = ParallelToolExecutor()
        actions: list[Any] = [MagicMock() for _ in range(6)]
        concurrent_count: list[int] = []
        lock = threading.Lock()
        current = [0]

        def tool_runner(action: Any) -> Any:
            with lock:
                current[0] += 1
                concurrent_count.append(current[0])
            time.sleep(0.02)
            with lock:
                current[0] -= 1
            return MagicMock()

        executor.execute_batch(actions, tool_runner)  # type: ignore[arg-type]

        # Should be limited by semaphore's max_concurrent=2
        assert max(concurrent_count) <= 2

    def test_exception_propagation(self):
        """Test that exceptions from tool_runner are propagated."""
        executor = ParallelToolExecutor()
        actions: list[Any] = [MagicMock()]

        def tool_runner(action: Any) -> Any:
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            executor.execute_batch(actions, tool_runner)  # type: ignore[arg-type]
