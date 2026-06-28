"""Tests that sub_event_sink and parent_tool_use_id are threaded through the task tool."""
import inspect

from openhands.tools.task.manager import TaskManager
from openhands.tools.task.impl import TaskExecutor


def test_taskmanager_accepts_sub_event_sink():
    sig = inspect.signature(TaskManager.__init__)
    assert "sub_event_sink" in sig.parameters


def test_start_task_accepts_parent_tool_use_id():
    sig = inspect.signature(TaskManager.start_task)
    assert "parent_tool_use_id" in sig.parameters


def test_executor_call_accepts_parent_tool_use_id():
    sig = inspect.signature(TaskExecutor.__call__)
    assert "parent_tool_use_id" in sig.parameters
