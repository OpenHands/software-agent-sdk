"""Tests for TaskTracker persistence and stable task identifiers."""

import json
from uuid import UUID

from openhands.tools.task_tracker import TaskTrackerAction, TaskTrackerExecutor
from openhands.tools.task_tracker.definition import TaskItem


def test_task_items_receive_unique_ids() -> None:
    tasks = [
        TaskItem(title="Inspect the repository", notes="", status="todo")
        for _ in range(2)
    ]

    assert UUID(tasks[0].id)
    assert UUID(tasks[1].id)
    assert tasks[0].id != tasks[1].id


def test_task_tracker_persists_task_ids_and_statuses(tmp_path) -> None:
    tasks = [
        TaskItem(
            title="Inspect the repository",
            notes="Read the project instructions first.",
            status="done",
        ),
        TaskItem(title="Implement the change", notes="", status="in_progress"),
    ]

    executor = TaskTrackerExecutor(save_dir=str(tmp_path))
    executor(TaskTrackerAction(command="plan", task_list=tasks))

    restored_executor = TaskTrackerExecutor(save_dir=str(tmp_path))
    observation = restored_executor(TaskTrackerAction(command="view"))

    assert observation.task_list == tasks
    assert all(f"[{task.id}]" in observation.text for task in tasks)


def test_task_tracker_preserves_ids_supplied_during_updates() -> None:
    task = TaskItem(title="Implement the change", notes="", status="todo")
    executor = TaskTrackerExecutor()
    executor(TaskTrackerAction(command="plan", task_list=[task]))

    updated_task = TaskItem(
        id=task.id,
        title=task.title,
        notes="Implemented and verified.",
        status="done",
    )
    observation = executor(TaskTrackerAction(command="plan", task_list=[updated_task]))

    assert observation.task_list[0].id == task.id
    assert observation.task_list[0].status == "done"


def test_task_tracker_loads_legacy_tasks_without_ids(tmp_path) -> None:
    (tmp_path / "TASKS.json").write_text(
        json.dumps([{"title": "Existing task", "notes": "", "status": "todo"}])
    )

    executor = TaskTrackerExecutor(save_dir=str(tmp_path))
    observation = executor(TaskTrackerAction(command="view"))

    assert len(observation.task_list) == 1
    assert UUID(observation.task_list[0].id)
    assert "[" + observation.task_list[0].id + "]" in observation.text


def test_task_tracker_action_loads_legacy_payload_without_ids() -> None:
    action = TaskTrackerAction.model_validate(
        {
            "command": "plan",
            "task_list": [{"title": "Existing task", "notes": "", "status": "todo"}],
        }
    )

    assert UUID(action.task_list[0].id)
