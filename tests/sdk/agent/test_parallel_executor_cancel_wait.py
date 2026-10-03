"""Regression tests for ParallelToolExecutor cancellation while lock-waiting.

Reproduces #4777: a tool call that blocks acquiring a resource lock and is
cancelled during that wait must not run once the lock becomes available.
"""

import asyncio
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from openhands.sdk.agent.parallel_executor import ParallelToolExecutor
from openhands.sdk.conversation.cancellation import CancellationToken
from openhands.sdk.conversation.resource_lock_manager import ResourceLockManager
from openhands.sdk.event.llm_convertible import AgentErrorEvent
from openhands.sdk.tool.tool import DeclaredResources


def _make_action(
    tool_name: str = "editor",
    tool_call_id: str = "call_1",
) -> Any:
    """Create a mock ActionEvent."""
    ae = MagicMock()
    ae.tool_name = tool_name
    ae.tool_call_id = tool_call_id
    ae.action = MagicMock()
    return ae


def _ok_event() -> Any:
    return MagicMock()


@pytest.mark.parametrize(
    ("resources", "lock_key", "use_async"),
    [
        pytest.param(
            DeclaredResources(
                keys=("file:/tmp/cancelled-waiter",),
                declared=True,
            ),
            "file:/tmp/cancelled-waiter",
            False,
            id="sync-declared-resource",
        ),
        pytest.param(
            DeclaredResources(keys=(), declared=False),
            "tool:editor",
            False,
            id="sync-tool-mutex",
        ),
        pytest.param(
            DeclaredResources(
                keys=("file:/tmp/cancelled-async-waiter",),
                declared=True,
            ),
            "file:/tmp/cancelled-async-waiter",
            True,
            id="async-declared-resource",
        ),
    ],
)
def test_cancellation_while_waiting_for_lock_skips_tool(
    resources: DeclaredResources,
    lock_key: str,
    use_async: bool,
):
    lock_mgr = ResourceLockManager(timeouts={"file": 1.0, "tool": 1.0})
    executor = ParallelToolExecutor(max_workers=2, lock_manager=lock_mgr)
    action = _make_action("editor", "c0")
    token = CancellationToken()
    resources_resolved = threading.Event()
    runner_called = threading.Event()

    tool = MagicMock()
    tool.name = "editor"

    def declared_resources(_: Any) -> DeclaredResources:
        resources_resolved.set()
        return resources

    tool.declared_resources = declared_resources

    def runner(_: Any) -> list[Any]:
        runner_called.set()
        return [_ok_event()]

    results: list[list[Any]] = []

    def execute() -> None:
        if use_async:
            batch = asyncio.run(
                executor.aexecute_batch(
                    [action],
                    runner,
                    {"editor": tool},
                    token,
                )
            )
        else:
            batch = executor.execute_batch(
                [action],
                runner,
                {"editor": tool},
                token,
            )
        results.extend(batch)

    worker = threading.Thread(target=execute)
    with lock_mgr.lock(lock_key):
        worker.start()
        assert resources_resolved.wait(timeout=1)
        token.cancel()

    worker.join(timeout=1)

    assert not worker.is_alive()
    assert not runner_called.is_set()
    assert len(results) == 1
    assert len(results[0]) == 1
    assert isinstance(results[0][0], AgentErrorEvent)
    assert results[0][0].error == "Tool call cancelled by interrupt."


def test_uncancelled_tool_runs_after_resource_lock_wait():
    lock_mgr = ResourceLockManager(timeouts={"file": 1.0})
    executor = ParallelToolExecutor(max_workers=2, lock_manager=lock_mgr)
    action = _make_action("editor", "c0")
    token = CancellationToken()
    resources_resolved = threading.Event()
    started = threading.Event()
    tool_output = _ok_event()
    resource_key = "file:/tmp/sanity-waiter"

    tool = MagicMock()
    tool.name = "editor"

    def declared_resources(_: Any) -> DeclaredResources:
        resources_resolved.set()
        return DeclaredResources(keys=(resource_key,), declared=True)

    tool.declared_resources = declared_resources

    def runner(_: Any) -> list[Any]:
        started.set()
        return [tool_output]

    results: list[list[Any]] = []

    def execute() -> None:
        results.extend(
            executor.execute_batch([action], runner, {"editor": tool}, token)
        )

    worker = threading.Thread(target=execute)
    with lock_mgr.lock(resource_key):
        worker.start()
        assert resources_resolved.wait(timeout=1)

    worker.join(timeout=5)

    assert not worker.is_alive()
    assert started.is_set()
    assert results[0] == [tool_output]
