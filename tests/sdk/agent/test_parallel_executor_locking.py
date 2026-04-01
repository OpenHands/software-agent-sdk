"""Integration tests for ParallelToolExecutor resource locking."""

import threading
from typing import Any
from unittest.mock import MagicMock

from openhands.sdk.agent.parallel_executor import ParallelToolExecutor
from openhands.sdk.conversation.resource_lock_manager import ResourceLockManager
from openhands.sdk.tool.tool import DeclaredResources, ToolAnnotations


_SENTINEL = object()


def _make_action(
    tool_name: str = "my_tool",
    tool_call_id: str = "call_1",
    action: Any = _SENTINEL,
) -> Any:
    """Create a mock ActionEvent."""
    ae = MagicMock()
    ae.tool_name = tool_name
    ae.tool_call_id = tool_call_id
    ae.action = MagicMock() if action is _SENTINEL else action
    return ae


def _make_tool(
    name: str = "my_tool",
    resources: DeclaredResources | None = None,
) -> Any:
    """Create a mock ToolDefinition."""
    tool = MagicMock()
    tool.name = name
    tool.annotations = ToolAnnotations()
    if resources is None:
        resources = DeclaredResources(keys=(), declared=False)
    tool.declared_resources = MagicMock(return_value=resources)
    return tool


def _ok_event() -> Any:
    return MagicMock()


# ── Undeclared resources → tool-wide mutex ────────────────────────


def test_undeclared_resources_serializes_via_tool_mutex():
    """declared=False → tool-wide serialization."""
    lock_mgr = ResourceLockManager()
    executor = ParallelToolExecutor(max_workers=4, lock_manager=lock_mgr)
    actions = [_make_action("editor", f"c{i}") for i in range(4)]
    tool = _make_tool(
        "editor",
        resources=DeclaredResources(keys=(), declared=False),
    )
    tools = {"editor": tool}

    log: list[str] = []
    lock = threading.Lock()

    def runner(a: Any) -> list[Any]:
        with lock:
            log.append(f"{a.tool_call_id}-enter")
        with lock:
            log.append(f"{a.tool_call_id}-exit")
        return [_ok_event()]

    executor.execute_batch(actions, runner, tools)

    assert len(log) == 8


# ── Declared with no keys → no locking ───────────────────────────


def test_declared_empty_keys_skips_locking():
    """declared=True with empty keys → no locking needed."""
    lock_mgr = ResourceLockManager()
    executor = ParallelToolExecutor(max_workers=4, lock_manager=lock_mgr)
    actions = [_make_action("think", f"c{i}") for i in range(4)]
    tool = _make_tool(
        "think",
        resources=DeclaredResources(keys=(), declared=True),
    )
    tools = {"think": tool}

    barrier = threading.Barrier(4, timeout=5)
    reached = [False] * 4

    def runner(a: Any) -> list[Any]:
        idx = int(a.tool_call_id[1])
        reached[idx] = True
        barrier.wait()  # all 4 must reach here concurrently
        return [_ok_event()]

    executor.execute_batch(actions, runner, tools)
    assert all(reached)


# ── Specific resource keys → per-resource locking ────────────────


def test_specific_resource_keys_serialize_same_resource():
    """Tools on the same file serialize; different files can overlap."""
    lock_mgr = ResourceLockManager()
    executor = ParallelToolExecutor(max_workers=4, lock_manager=lock_mgr)

    a0 = _make_action("editor", "c0")
    a1 = _make_action("editor", "c1")
    a2 = _make_action("editor", "c2")
    a3 = _make_action("editor", "c3")

    tool = MagicMock()
    tool.name = "editor"
    tool.annotations = ToolAnnotations(readOnlyHint=False)

    call_count = [0]

    def declared_res(action: Any) -> DeclaredResources:
        idx = call_count[0]
        call_count[0] += 1
        key = f"file:/{chr(ord('a') + idx // 2)}.py"
        return DeclaredResources(keys=(key,), declared=True)

    tool.declared_resources = declared_res
    tools: Any = {"editor": tool}

    events = [_ok_event() for _ in range(4)]
    results = executor.execute_batch(
        [a0, a1, a2, a3],
        lambda a: [events[int(a.tool_call_id[1])]],
        tools,
    )

    assert len(results) == 4


# ── No tools dict → locking skipped entirely ─────────────────────


def test_no_tools_dict_skips_locking():
    """When tools=None, execute without any locking (backward compat)."""
    executor = ParallelToolExecutor(max_workers=4)
    actions = [_make_action("x", f"c{i}") for i in range(3)]

    results = executor.execute_batch(actions, lambda a: [_ok_event()])

    assert len(results) == 3


# ── action.action is None → tool-wide mutex ──────────────────────


def test_none_action_falls_back_to_tool_mutex():
    """ActionEvent with action=None should use tool-wide mutex."""
    lock_mgr = ResourceLockManager()
    executor = ParallelToolExecutor(max_workers=2, lock_manager=lock_mgr)
    ae = _make_action("editor", "c0", action=None)
    tool = _make_tool(
        "editor",
        resources=DeclaredResources(
            keys=("file:/x",),
            declared=True,
        ),
    )
    tools = {"editor": tool}

    results = executor.execute_batch([ae], lambda a: [_ok_event()], tools)

    assert len(results) == 1
    tool.declared_resources.assert_not_called()
