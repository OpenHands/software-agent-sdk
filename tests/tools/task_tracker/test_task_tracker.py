"""Unit tests for the task_tracker tool executor."""

import json
from pathlib import Path

from openhands.tools.task_tracker.definition import (
    TaskItem,
    TaskTrackerAction,
    TaskTrackerExecutor,
)


def test_view_empty_task_list() -> None:
    executor = TaskTrackerExecutor()
    observation = executor(TaskTrackerAction(command="view"))

    assert observation.is_error is False
    assert observation.command == "view"
    assert observation.task_list == []
    assert "No task list found" in observation.text


def test_plan_and_view_task_list() -> None:
    executor = TaskTrackerExecutor()
    tasks = [
        TaskItem(title="Write tests", notes="", status="in_progress"),
        TaskItem(title="Update docs", notes="README only", status="todo"),
    ]

    plan_obs = executor(
        TaskTrackerAction(command="plan", task_list=tasks),
    )
    assert plan_obs.is_error is False
    assert plan_obs.command == "plan"
    assert len(plan_obs.task_list) == 2

    view_obs = executor(TaskTrackerAction(command="view"))
    assert view_obs.is_error is False
    assert view_obs.task_list[0].title == "Write tests"
    assert view_obs.task_list[1].notes == "README only"
    assert "Write tests" in view_obs.text
    assert "Update docs" in view_obs.text


def test_plan_persists_tasks_to_json(tmp_path: Path) -> None:
    executor = TaskTrackerExecutor(save_dir=str(tmp_path))
    tasks = [
        TaskItem(title="Ship feature", notes="", status="done"),
        TaskItem(title="Run validation", notes="", status="todo"),
    ]

    executor(TaskTrackerAction(command="plan", task_list=tasks))

    tasks_file = tmp_path / "TASKS.json"
    assert tasks_file.exists()
    persisted = json.loads(tasks_file.read_text(encoding="utf-8"))
    assert persisted[0]["title"] == "Ship feature"
    assert persisted[1]["status"] == "todo"


def test_load_tasks_from_existing_json(tmp_path: Path) -> None:
    tasks_file = tmp_path / "TASKS.json"
    tasks_file.write_text(
        json.dumps(
            [
                {"title": "Resume work", "notes": "", "status": "in_progress"},
            ]
        ),
        encoding="utf-8",
    )

    executor = TaskTrackerExecutor(save_dir=str(tmp_path))
    observation = executor(TaskTrackerAction(command="view"))

    assert observation.task_list[0].title == "Resume work"
    assert observation.task_list[0].status == "in_progress"


def test_load_tasks_ignores_invalid_json(tmp_path: Path) -> None:
    tasks_file = tmp_path / "TASKS.json"
    tasks_file.write_text("{not valid json", encoding="utf-8")

    executor = TaskTrackerExecutor(save_dir=str(tmp_path))
    observation = executor(TaskTrackerAction(command="view"))

    assert observation.task_list == []
