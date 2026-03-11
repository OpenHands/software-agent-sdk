"""Parallel tool execution for agent.

This module provides classes for executing multiple tool calls concurrently
with a configurable global concurrency limit.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Final

from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.event.llm_convertible import (
        ActionEvent,
        AgentErrorEvent,
        ObservationEvent,
        UserRejectObservation,
    )

    # Type alias for tool execution results
    ToolExecutionResult = ObservationEvent | AgentErrorEvent | UserRejectObservation


logger = get_logger(__name__)

# Default concurrency limit for tool executions (process-wide)
DEFAULT_TOOL_CONCURRENCY_LIMIT: Final[int] = 8

# Environment variable name for configuring concurrency limit
ENV_TOOL_CONCURRENCY_LIMIT: Final[str] = "OPENHANDS_TOOL_CONCURRENCY_LIMIT"


class ToolExecutorSemaphore:
    """Process-global semaphore that limits concurrent tool executions.

    This singleton ensures that the total number of concurrent tool executions
    across all agents and sub-agents does not exceed a configurable limit.

    The concurrency limit can be configured via:
    - Environment variable: OPENHANDS_TOOL_CONCURRENCY_LIMIT
    - Default: 8

    Example:
        >>> semaphore = ToolExecutorSemaphore()
        >>> with semaphore:
        ...     # Execute tool - at most max_concurrent tools run at once
        ...     result = execute_tool(action)
    """

    _instance: ToolExecutorSemaphore | None = None
    _lock: threading.Lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> ToolExecutorSemaphore:
        """Create or return the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                # Double-check locking pattern
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the semaphore (only runs once for singleton)."""
        if self._initialized:
            return

        self._max_concurrent = self._resolve_max_concurrent()
        self._semaphore = threading.Semaphore(self._max_concurrent)
        self._initialized = True
        logger.debug(f"ToolExecutorSemaphore initialized: {self._max_concurrent=}")

    def _resolve_max_concurrent(self) -> int:
        """Resolve max_concurrent from environment variable or default."""
        env_value = os.environ.get(ENV_TOOL_CONCURRENCY_LIMIT)
        if env_value is None:
            return DEFAULT_TOOL_CONCURRENCY_LIMIT

        try:
            value = int(env_value)
            if value <= 0:
                logger.warning(
                    f"{ENV_TOOL_CONCURRENCY_LIMIT}={env_value} is invalid, "
                    f"using default {DEFAULT_TOOL_CONCURRENCY_LIMIT}"
                )
                return DEFAULT_TOOL_CONCURRENCY_LIMIT
            return value
        except ValueError:
            logger.warning(
                f"{ENV_TOOL_CONCURRENCY_LIMIT}={env_value} is not a valid "
                f"integer, using default {DEFAULT_TOOL_CONCURRENCY_LIMIT}"
            )
            return DEFAULT_TOOL_CONCURRENCY_LIMIT

    @property
    def max_concurrent(self) -> int:
        """Return the maximum concurrent limit."""
        return self._max_concurrent

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        """Acquire a slot in the semaphore."""
        return self._semaphore.acquire(blocking=blocking, timeout=timeout)

    def release(self) -> None:
        """Release a slot in the semaphore."""
        self._semaphore.release()

    def __enter__(self) -> ToolExecutorSemaphore:
        """Context manager entry."""
        self._semaphore.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Context manager exit."""
        self._semaphore.release()


class ParallelToolExecutor:
    """Executes a batch of tool calls concurrently.

    This executor runs multiple tool calls in parallel using a ThreadPoolExecutor,
    with concurrency limited by ToolExecutorSemaphore (configured via
    OPENHANDS_TOOL_CONCURRENCY_LIMIT environment variable).

    Results are collected and returned in the original order regardless of
    completion order.

    Example:
        >>> executor = ParallelToolExecutor()
        >>> results = executor.execute_batch(
        ...     action_events=[action1, action2, action3],
        ...     tool_runner=my_tool_runner_func
        ... )
        >>> # results[0] corresponds to action1, results[1] to action2, etc.
    """

    def __init__(self) -> None:
        """Initialize the parallel executor."""
        self._semaphore = ToolExecutorSemaphore()

    def execute_batch(
        self,
        action_events: Sequence[ActionEvent],
        tool_runner: Callable[[ActionEvent], ToolExecutionResult],
    ) -> list[ToolExecutionResult]:
        """Execute a batch of action events concurrently.

        Args:
            action_events: Sequence of ActionEvent objects to execute.
            tool_runner: A callable that takes an ActionEvent and returns
                        a ToolExecutionResult (ObservationEvent, AgentErrorEvent,
                        or UserRejectObservation).

        Returns:
            List of execution results in the same order as the input action_events.
        """
        if not action_events:
            return []

        # For single action, no need for thread pool overhead
        if len(action_events) == 1:
            return [self._run_with_semaphore(action_events[0], tool_runner)]

        # Execute actions in parallel, semaphore controls actual concurrency
        results: list[ToolExecutionResult | None] = [None] * len(action_events)

        with ThreadPoolExecutor(max_workers=len(action_events)) as executor:
            # Submit all tasks and map futures to their indices
            future_to_index = {
                executor.submit(
                    self._run_with_semaphore, action_event, tool_runner
                ): idx
                for idx, action_event in enumerate(action_events)
            }

            # Collect results as they complete
            for future in future_to_index:
                idx = future_to_index[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    # This shouldn't happen if tool_runner handles exceptions properly
                    # but we need to handle it gracefully
                    logger.error(
                        f"Unexpected error executing action at index {idx}: {e}"
                    )
                    raise

        # Type narrowing: all results should be non-None after completion
        return [r for r in results if r is not None]

    def _run_with_semaphore(
        self,
        action_event: ActionEvent,
        tool_runner: Callable[[ActionEvent], ToolExecutionResult],
    ) -> ToolExecutionResult:
        """Run a tool with semaphore protection."""
        with self._semaphore:
            return tool_runner(action_event)
