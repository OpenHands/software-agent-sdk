"""Parallel tool execution for agent.

This module provides utilities for executing multiple tool calls concurrently
with a configurable per-agent concurrency limit.

.. warning:: Thread safety of individual tools

   When ``tool_concurrency_limit > 1``, multiple tools run in parallel
   threads sharing the same ``conversation`` object. Tools are **not**
   thread-safe by default — concurrent mutations to working directory,
   filesystem, or conversation state can race. Callers opting into
   parallelism must ensure the tools in use are safe for concurrent
   execution.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from openhands.sdk.event.llm_convertible import AgentErrorEvent
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.event.base import Event
    from openhands.sdk.event.llm_convertible import ActionEvent

logger = get_logger(__name__)


class ParallelToolExecutor:
    """Executes a batch of tool calls concurrently.

    Each instance has its own thread pool and concurrency limit, so
    nested execution (e.g., subagents) cannot deadlock the parent.

    .. warning::

       When concurrency > 1, tools share the ``conversation`` object
       across threads. Tools are not thread-safe by default — concurrent
       mutations to filesystem, working directory, or conversation state
       can cause race conditions.
    """

    def __init__(self, max_workers: int = 1) -> None:
        self._max_workers = max_workers

    def execute_batch(
        self,
        action_events: Sequence[ActionEvent],
        tool_runner: Callable[[ActionEvent], list[Event]],
    ) -> list[list[Event]]:
        """Execute a batch of action events concurrently.

        Args:
            action_events: Sequence of ActionEvent objects to execute.
            tool_runner: A callable that takes an ActionEvent and returns
                        a list of Event objects produced by the execution.

        Returns:
            List of event lists in the same order as the input action_events.
        """
        if not action_events:
            return []

        if len(action_events) == 1 or self._max_workers == 1:
            return [self._run_safe(action, tool_runner) for action in action_events]

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [
                executor.submit(self._run_safe, action, tool_runner)
                for action in action_events
            ]

        return [future.result() for future in futures]

    @staticmethod
    def _run_safe(
        action: ActionEvent,
        tool_runner: Callable[[ActionEvent], list[Event]],
    ) -> list[Event]:
        """Run tool_runner, converting exceptions to AgentErrorEvent.

        All exceptions are caught so that one failing tool in a parallel
        batch cannot crash the agent or prevent sibling tools from
        completing.  ValueErrors are expected tool errors (bad arguments,
        validation failures); anything else is likely a programming bug
        and is logged at ERROR with a full traceback.
        """
        try:
            return tool_runner(action)
        except ValueError as e:
            # Expected tool errors (invalid arguments, precondition failures, etc.)
            logger.info(f"Tool error in '{action.tool_name}': {e}")
            return [
                AgentErrorEvent(
                    error=f"Error executing tool '{action.tool_name}': {e}",
                    tool_name=action.tool_name,
                    tool_call_id=action.tool_call_id,
                )
            ]
        except Exception as e:
            # Unexpected errors — likely bugs in tool implementations.
            # Logged at ERROR with traceback to aid debugging.
            logger.error(
                f"Unexpected error in tool '{action.tool_name}': {e}",
                exc_info=True,
            )
            return [
                AgentErrorEvent(
                    error=f"Error executing tool '{action.tool_name}': {e}",
                    tool_name=action.tool_name,
                    tool_call_id=action.tool_call_id,
                )
            ]
