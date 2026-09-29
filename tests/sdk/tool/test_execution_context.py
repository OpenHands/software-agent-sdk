"""Tool invocation scopes must not leak across nested or concurrent execution."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from openhands.sdk.tool.execution_context import (
    ToolInvocation,
    get_current_tool_invocation,
    tool_invocation_context,
)


def _invocation(name: str) -> ToolInvocation:
    return ToolInvocation(action_id=name, tool_call_id=f"call-{name}")


def test_nested_scope_restores_after_exception() -> None:
    assert get_current_tool_invocation() is None
    outer = _invocation("outer")
    with tool_invocation_context(outer):
        with pytest.raises(ValueError, match="failed"):
            with tool_invocation_context(_invocation("inner")):
                assert get_current_tool_invocation() == _invocation("inner")
                raise ValueError("failed")
        assert get_current_tool_invocation() == outer
    assert get_current_tool_invocation() is None


def test_parallel_threads_have_independent_scopes() -> None:
    barrier = threading.Barrier(2, timeout=5)

    def run(name: str) -> ToolInvocation | None:
        assert get_current_tool_invocation() is None
        with tool_invocation_context(_invocation(name)):
            barrier.wait()
            current = get_current_tool_invocation()
        assert get_current_tool_invocation() is None
        return current

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, ["first", "second"])) == [
            _invocation("first"),
            _invocation("second"),
        ]


async def test_async_tasks_have_independent_scopes() -> None:
    ready = asyncio.Event()
    entered = 0

    async def run(name: str) -> ToolInvocation | None:
        nonlocal entered
        with tool_invocation_context(_invocation(name)):
            entered += 1
            if entered == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
            return get_current_tool_invocation()

    with tool_invocation_context(_invocation("caller")):
        assert await asyncio.gather(run("first"), run("second")) == [
            _invocation("first"),
            _invocation("second"),
        ]
        assert get_current_tool_invocation() == _invocation("caller")
    assert get_current_tool_invocation() is None
