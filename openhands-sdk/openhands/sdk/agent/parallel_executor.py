"""Parallel tool execution for agent.

This module provides utilities for executing multiple tool calls concurrently
with a configurable per-agent concurrency limit and resource-level locking.

Resource locking (via ``ResourceLockManager``) ensures that tools operating on
the same shared state (files, terminal session, browser, …) are serialized,
while tools touching *different* resources can run concurrently.

.. warning:: Thread safety of individual tools

   When ``tool_concurrency_limit > 1``, multiple tools run in parallel
   threads sharing the same ``conversation`` object. The executor uses
   ``ResourceLockManager`` to serialize access to shared resources, but
   tools must correctly implement ``declared_resources()`` for this
   to be effective.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from openhands.sdk.conversation.resource_lock_manager import ResourceLockManager
from openhands.sdk.event.llm_convertible import AgentErrorEvent
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.event.base import Event
    from openhands.sdk.event.llm_convertible import ActionEvent
    from openhands.sdk.tool.tool import DeclaredResources, ToolDefinition

logger = get_logger(__name__)


class ParallelToolExecutor:
    """Executes a batch of tool calls concurrently with resource locking.

    Each instance has its own thread pool, concurrency limit, and
    ``ResourceLockManager``, so nested execution (e.g., subagents) cannot
    deadlock the parent.
    """

    def __init__(
        self,
        max_workers: int = 1,
        lock_manager: ResourceLockManager | None = None,
    ) -> None:
        self._max_workers = max_workers
        self._lock_manager = lock_manager or ResourceLockManager()

    def execute_batch(
        self,
        action_events: Sequence[ActionEvent],
        tool_runner: Callable[[ActionEvent], list[Event]],
        tools: dict[str, ToolDefinition] | None = None,
    ) -> list[list[Event]]:
        """Execute a batch of action events concurrently.

        Args:
            action_events: Sequence of ActionEvent objects to execute.
            tool_runner: A callable that takes an ActionEvent and returns
                        a list of Event objects produced by the execution.
            tools: Optional mapping of tool name to ToolDefinition used
                   to derive resource keys for locking. When *None*,
                   locking is skipped (backward-compatible).

        Returns:
            List of event lists in the same order as the input action_events.
        """
        if not action_events:
            return []

        def _resolve(ae: ActionEvent) -> ToolDefinition | None:
            return tools.get(ae.tool_name) if tools else None

        if len(action_events) == 1 or self._max_workers == 1:
            return [
                self._run_safe(action, tool_runner, _resolve(action))
                for action in action_events
            ]

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [
                executor.submit(self._run_safe, action, tool_runner, _resolve(action))
                for action in action_events
            ]

        return [future.result() for future in futures]

    def _run_safe(
        self,
        action: ActionEvent,
        tool_runner: Callable[[ActionEvent], list[Event]],
        tool: ToolDefinition | None = None,
    ) -> list[Event]:
        """Run tool_runner with resource locking.

        Converts exceptions to ``AgentErrorEvent``.

        Locking strategy:

        - ``declared=False`` → ``tool:<name>`` mutex.
        - ``declared=True``, empty keys → no locking.
        - ``declared=True``, keys present → lock those resources.
        """
        try:
            if tool is None:
                return tool_runner(action)

            resources = self._extract_declared_resources(action, tool)
            lock_keys = self._resolve_lock_keys(resources, tool)
            if not lock_keys:
                return tool_runner(action)
            with self._lock_manager.lock(*lock_keys):
                return tool_runner(action)

        except ValueError as e:
            logger.info(f"Tool error in '{action.tool_name}': {e}")
            return [
                AgentErrorEvent(
                    error=f"Error executing tool '{action.tool_name}': {e}",
                    tool_name=action.tool_name,
                    tool_call_id=action.tool_call_id,
                )
            ]
        except Exception as e:
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

    @staticmethod
    def _extract_declared_resources(
        action: ActionEvent,
        tool: ToolDefinition,
    ) -> DeclaredResources | None:
        """Call ``tool.declared_resources()`` if the action is parsed."""
        parsed_action = action.action
        return tool.declared_resources(parsed_action) if parsed_action else None

    @staticmethod
    def _resolve_lock_keys(
        resources: DeclaredResources | None,
        tool: ToolDefinition,
    ) -> list[str]:
        """Turn declared resources into lock keys.

        Returns an empty list when no locking is needed.
        """
        if resources is None or not resources.declared:
            return [f"tool:{tool.name}"]
        return list(resources.keys)
