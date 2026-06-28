"""Tests for TaskManager._make_forwarding_callback (Task 3)."""

import inspect

from openhands.tools.task.manager import TaskManager


def test_forwarding_callback_stamps_and_forwards(monkeypatch):
    captured = []
    mgr = TaskManager(sub_event_sink=lambda e: captured.append(e))
    cb = mgr._make_forwarding_callback(parent_tool_use_id="toolu_abc")

    class FakeEvent:
        parent_tool_use_id = None

    ev = FakeEvent()
    cb(ev)

    assert ev.parent_tool_use_id == "toolu_abc"
    assert captured == [ev]


def test_no_sink_means_no_callback():
    mgr = TaskManager(sub_event_sink=None)
    assert mgr._make_forwarding_callback(parent_tool_use_id="x") is None


def test_forwarding_does_not_touch_parent_state():
    # The callback only stamps + forwards; it must not reference parent state.
    src = inspect.getsource(TaskManager._make_forwarding_callback)
    assert "state.events" not in src and "append" not in src


def test_forwarding_callback_swallows_sink_errors(monkeypatch):
    """Best-effort: a failing sink must not crash the sub-agent run."""

    def bad_sink(event):
        raise RuntimeError("sink exploded")

    mgr = TaskManager(sub_event_sink=bad_sink)
    cb = mgr._make_forwarding_callback(parent_tool_use_id="toolu_xyz")

    class FakeEvent:
        parent_tool_use_id = None

    ev = FakeEvent()
    # Must not raise
    cb(ev)
    # stamp still applied even though sink raised
    assert ev.parent_tool_use_id == "toolu_xyz"
