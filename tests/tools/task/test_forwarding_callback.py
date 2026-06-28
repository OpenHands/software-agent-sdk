"""Tests for TaskManager._make_forwarding_callback (Task 3)."""

import inspect

import pytest

from openhands.sdk.event.llm_convertible.message import MessageEvent
from openhands.sdk.llm import Message
from openhands.tools.task.manager import TaskManager


def _make_event() -> MessageEvent:
    """Return a real frozen Pydantic Event instance."""
    return MessageEvent(
        source="user",
        llm_message=Message(role="user"),
    )


def test_forwarding_callback_stamps_and_forwards():
    """Forwarding callback produces a stamped copy and delivers it to the sink."""
    captured = []
    mgr = TaskManager(sub_event_sink=lambda e: captured.append(e))
    cb = mgr._make_forwarding_callback(parent_tool_use_id="toolu_abc")

    ev = _make_event()
    cb(ev)

    # The original event must be unmodified (frozen model, immutable)
    assert ev.parent_tool_use_id is None

    # The stamped copy is delivered to the sink
    assert len(captured) == 1
    stamped = captured[0]
    assert stamped is not ev
    assert stamped.parent_tool_use_id == "toolu_abc"


def test_no_sink_means_no_callback():
    mgr = TaskManager(sub_event_sink=None)
    assert mgr._make_forwarding_callback(parent_tool_use_id="x") is None


def test_forwarding_does_not_touch_parent_state():
    # The callback only stamps + forwards; it must not reference parent state.
    src = inspect.getsource(TaskManager._make_forwarding_callback)
    assert "state.events" not in src and "append" not in src


def test_forwarding_callback_swallows_sink_errors():
    """Best-effort: a failing sink must not crash the sub-agent run."""

    def bad_sink(event):
        raise RuntimeError("sink exploded")

    mgr = TaskManager(sub_event_sink=bad_sink)
    cb = mgr._make_forwarding_callback(parent_tool_use_id="toolu_xyz")

    ev = _make_event()
    # Must not raise even though the sink errors
    cb(ev)


def test_forwarding_callback_uses_model_copy_not_setattr():
    """Regression: frozen Pydantic Event must be stamped via model_copy, not setattr.

    Setting a field on a frozen model raises ValidationError. The forwarding
    callback must use model_copy(update=...) to produce a new instance.
    """
    captured = []
    mgr = TaskManager(sub_event_sink=lambda e: captured.append(e))
    cb = mgr._make_forwarding_callback(parent_tool_use_id="toolu_frozen_test")

    # Use a real frozen Pydantic event (parent_tool_use_id defaults to None)
    ev = _make_event()
    assert ev.parent_tool_use_id is None  # frozen, field is set at construction

    # Must not raise (the old code raised ValidationError: frozen_instance)
    cb(ev)

    # The stamped copy reaches the sink
    assert len(captured) == 1
    stamped = captured[0]
    assert stamped.parent_tool_use_id == "toolu_frozen_test"
    # Original event is unchanged
    assert ev.parent_tool_use_id is None


def test_forwarding_callback_real_event_frozen_setattr_would_fail():
    """Confirm that direct setattr on the frozen event raises, so model_copy is required."""
    from pydantic import ValidationError

    ev = _make_event()
    with pytest.raises(ValidationError, match="frozen"):
        ev.parent_tool_use_id = "x"  # type: ignore[misc]
