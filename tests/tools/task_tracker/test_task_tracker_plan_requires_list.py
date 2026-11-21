"""Regression tests for the TaskTracker `plan` command and its task_list argument.

A `plan` call whose `task_list` is omitted must NOT silently overwrite the saved
list with an empty one and report success. The field is documented as required
for `plan`; omitting it is an error. An explicit empty list still clears the list.
"""

import json

from openhands.tools.task_tracker.definition import (
    TaskItem,
    TaskTrackerAction,
    TaskTrackerExecutor,
    TaskTrackerTool,
)


def _seed(executor: TaskTrackerExecutor) -> None:
    executor(
        TaskTrackerAction(
            command="plan",
            task_list=[
                TaskItem(title="Design API"),
                TaskItem(title="Implement", status="in_progress"),
            ],
        )
    )


def test_plan_with_omitted_task_list_errors_and_preserves_tasks(tmp_path):
    executor = TaskTrackerExecutor(save_dir=str(tmp_path))
    _seed(executor)

    # A model that calls `plan` without resending the list (the field is
    # omittable in the schema) must not wipe the saved tasks.
    obs = executor(TaskTrackerAction(command="plan"))

    assert obs.is_error is True
    assert "requires `task_list`" in obs.text
    # The saved list is untouched, on disk and in memory.
    assert len(obs.task_list) == 2
    assert json.load(open(tmp_path / "TASKS.json")) != []
    assert len(json.load(open(tmp_path / "TASKS.json"))) == 2


def test_plan_with_explicit_empty_list_still_clears(tmp_path):
    executor = TaskTrackerExecutor(save_dir=str(tmp_path))
    _seed(executor)

    obs = executor(TaskTrackerAction(command="plan", task_list=[]))

    assert obs.is_error is False
    assert obs.task_list == []
    assert json.load(open(tmp_path / "TASKS.json")) == []


def test_omitted_task_list_deserializes_to_none_not_empty_list():
    # The exact arguments a model emits when it calls `plan` without task_list.
    action = TaskTrackerAction.model_validate_json('{"command":"plan"}')
    assert action.task_list is None


def test_plan_tool_is_marked_destructive(tmp_path):
    class _State:
        persistence_dir = str(tmp_path)

    tool = TaskTrackerTool.create(_State())[0]
    # `plan` overwrites/erases the saved list, so the tool is destructive.
    assert tool.annotations.destructiveHint is True
    assert tool.annotations.idempotentHint is True
