import json

from openhands.tools.update_plan import (
    PlanItem,
    UpdatePlanAction,
    UpdatePlanExecutor,
    UpdatePlanTool,
)


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


def test_update_plan_tool_responses_schema_matches_codex_shape(
    mock_conversation_state,
) -> None:
    tool = UpdatePlanTool.create(mock_conversation_state)[0]

    responses_tool = tool.to_responses_tool()
    assert responses_tool["name"] == "update_plan"
    assert responses_tool.get("description") == (
        "Updates the task plan.\n"
        "Provide an optional explanation and a list of plan items, each with a "
        "step and status.\n"
        "At most one step can be in_progress at a time.\n"
    )

    parameters = responses_tool["parameters"]
    assert isinstance(parameters, dict)
    required = parameters.get("required")
    assert isinstance(required, list)
    assert required == ["plan"]

    properties = parameters.get("properties")
    assert isinstance(properties, dict)
    assert "summary" in properties
    assert "explanation" not in required

    plan_schema = properties["plan"]
    assert isinstance(plan_schema, dict)
    assert plan_schema.get("description") == "The list of steps."

    item_schema = plan_schema.get("items")
    assert isinstance(item_schema, dict)
    assert item_schema.get("required") == ["step", "status"]

    item_properties = item_schema.get("properties")
    assert isinstance(item_properties, dict)
    status_schema = item_properties["status"]
    assert isinstance(status_schema, dict)
    assert status_schema.get("enum") == [
        "pending",
        "in_progress",
        "completed",
    ]
