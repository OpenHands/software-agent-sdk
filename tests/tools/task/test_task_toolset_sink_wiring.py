"""Tests that TaskToolSet.create passes sub_event_sink from conv_state to TaskManager."""
from unittest.mock import MagicMock
from openhands.tools.task import TaskToolSet
from openhands.tools.task.impl import TaskExecutor


def test_task_toolset_passes_sink_from_conv_state():
    """When conv_state has a sub_event_sink, it's passed to TaskManager."""
    my_sink = lambda event: None

    mock_state = MagicMock()
    mock_state.get_sub_event_sink.return_value = my_sink
    # Ensure hasattr works for our guard

    tools = TaskToolSet.create(conv_state=mock_state)
    assert len(tools) == 1
    tool = tools[0]

    # The tool has an executor; the executor has a manager
    assert isinstance(tool.executor, TaskExecutor)
    assert tool.executor._manager._sub_event_sink is my_sink


def test_task_toolset_none_sink_when_conv_state_none():
    """When conv_state is None (legacy), sink is None."""
    tools = TaskToolSet.create(conv_state=None)
    assert len(tools) == 1
    tool = tools[0]
    assert isinstance(tool.executor, TaskExecutor)
    assert tool.executor._manager._sub_event_sink is None
