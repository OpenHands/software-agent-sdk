import json

from openhands.tools.update_plan import PlanItem, UpdatePlanAction, UpdatePlanExecutor


def test_update_plan_executor_persists_plan_with_task_tracker_schema(tmp_path) -> None:
    executor = UpdatePlanExecutor(save_dir=str(tmp_path))
    action = UpdatePlanAction(
        explanation="Starting implementation",
        plan=[
            PlanItem(step="Inspect prompt flow", status="completed"),
            PlanItem(step="Wire update_plan tool", status="in_progress"),
            PlanItem(step="Run focused tests", status="pending"),
        ],
    )

    observation = executor(action)

    assert observation.is_error is False
    assert observation.explanation == "Starting implementation"
    assert [item.status for item in observation.plan] == [
        "completed",
        "in_progress",
        "pending",
    ]

    saved = json.loads((tmp_path / "TASKS.json").read_text())
    assert saved == [
        {"title": "Inspect prompt flow", "notes": "", "status": "done"},
        {"title": "Wire update_plan tool", "notes": "", "status": "in_progress"},
        {"title": "Run focused tests", "notes": "", "status": "todo"},
    ]
