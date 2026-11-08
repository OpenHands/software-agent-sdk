import pytest
from pydantic import ValidationError

from openhands.tools.task_tracker.definition import TaskTrackerAction


def _plan(status: str) -> TaskTrackerAction:
    return TaskTrackerAction.model_validate(
        {"command": "plan", "task_list": [{"title": "Review diff", "status": status}]}
    )


@pytest.mark.parametrize(
    "status,expected",
    [
        ("pending", "todo"),
        ("To Do", "todo"),
        ("TODO", "todo"),
        ("in-progress", "in_progress"),
        ("In Progress", "in_progress"),
        ("completed", "done"),
        ("Complete", "done"),
        ("done", "done"),
    ],
)
def test_plan_accepts_common_status_names(status, expected):
    assert _plan(status).task_list[0].status == expected


@pytest.mark.parametrize("status", ["blocked", "cancelled", ""])
def test_plan_still_rejects_unknown_status(status):
    with pytest.raises(ValidationError):
        _plan(status)
