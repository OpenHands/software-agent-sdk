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


def test_task_tracker_reconciles_id_for_unique_title_update() -> None:
    task = TaskItem(
        id="task-a",
        title="Implement the change",
        notes="",
        status="todo",
    )
    executor = TaskTrackerExecutor()
    executor(TaskTrackerAction(command="plan", task_list=[task]))

    updated_action = TaskTrackerAction.model_validate(
        {
            "command": "plan",
            "task_list": [
                {
                    "title": task.title,
                    "notes": "Implemented and verified.",
                    "status": "done",
                }
            ],
        }
    )
    observation = executor(updated_action)

    assert observation.task_list[0].id == task.id
    assert observation.task_list[0].status == "done"


def test_task_tracker_preserves_explicit_id_during_update() -> None:
    executor = TaskTrackerExecutor()
    executor(
        TaskTrackerAction(
            command="plan",
            task_list=[TaskItem(id="task-a", title="A", notes="", status="todo")],
        )
    )

    updated_task = TaskItem(id="task-new", title="A", notes="", status="done")
    observation = executor(TaskTrackerAction(command="plan", task_list=[updated_task]))

    assert observation.task_list[0].id == "task-new"


def test_task_tracker_generates_id_for_new_task_without_id() -> None:
    executor = TaskTrackerExecutor()
    executor(
        TaskTrackerAction(
            command="plan",
            task_list=[TaskItem(id="task-a", title="A", notes="", status="todo")],
        )
    )

    new_action = TaskTrackerAction.model_validate(
        {
            "command": "plan",
            "task_list": [{"title": "B", "notes": "", "status": "todo"}],
        }
    )
    observation = executor(new_action)

    assert UUID(observation.task_list[0].id)
    assert observation.task_list[0].id != "task-a"


def test_task_tracker_does_not_reconcile_ambiguous_title() -> None:
    executor = TaskTrackerExecutor()
    executor(
        TaskTrackerAction(
            command="plan",
            task_list=[
                TaskItem(id="task-a", title="Duplicate", notes="", status="todo"),
                TaskItem(id="task-b", title="Duplicate", notes="", status="todo"),
            ],
        )
    )

    ambiguous_action = TaskTrackerAction.model_validate(
        {
            "command": "plan",
            "task_list": [{"title": "Duplicate", "notes": "", "status": "done"}],
        }
    )
    observation = executor(ambiguous_action)

    assert UUID(observation.task_list[0].id)
    assert observation.task_list[0].id not in {"task-a", "task-b"}


def test_task_tracker_migrates_legacy_task_ids(tmp_path) -> None:
    (tmp_path / "TASKS.json").write_text(
        json.dumps([{"title": "Existing task", "notes": "", "status": "todo"}])
    )

    executor1 = TaskTrackerExecutor(save_dir=str(tmp_path))
    id1 = executor1(TaskTrackerAction(command="view")).task_list[0].id

    persisted_tasks = json.loads((tmp_path / "TASKS.json").read_text())
    assert persisted_tasks[0]["id"] == id1

    executor2 = TaskTrackerExecutor(save_dir=str(tmp_path))
    id2 = executor2(TaskTrackerAction(command="view")).task_list[0].id

    assert UUID(id1)
    assert id2 == id1


def test_task_tracker_action_loads_legacy_payload_without_ids() -> None:
    action = TaskTrackerAction.model_validate(
        {
            "command": "plan",
            "task_list": [{"title": "Existing task", "notes": "", "status": "todo"}],
        }
    )

    assert UUID(action.task_list[0].id)
