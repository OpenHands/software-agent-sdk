from openhands.sdk.tool.spec import Tool
from openhands.tools.preset import (
    TASK_OUTCOME_STATUSES,
    TaskOutcome,
    TaskOutcomeBlocker,
)


def test_task_outcome_accepts_outcome_summary_alias():
    outcome = TaskOutcome(
        status="blocked",
        outcome_summary="Could not continue without a token.",
        blockers=[
            TaskOutcomeBlocker(
                type="missing_secret",
                message="A required API token was not configured.",
                recoverable=True,
            )
        ],
        confidence=0.7,
        needs_user_action=True,
    )

    assert outcome.summary == "Could not continue without a token."
    assert outcome.blockers[0].type == "missing_secret"
    assert outcome.needs_user_action is True


def test_task_outcome_finish_tool_schema_uses_outcome_summary():
    schema = Tool(
        name="FinishTool", params={"response_schema": TaskOutcome}
    ).model_dump(mode="json")["params"]["response_schema"]

    properties = schema["properties"]
    assert "outcome_summary" in properties
    assert "summary" not in properties
    assert "source" not in properties
    assert "reported_at" not in properties
    assert "terminal_reason" not in properties

    assert properties["status"]["description"] == (
        "Agent's semantic assessment of task completion."
    )
    assert tuple(properties["status"]["enum"]) == TASK_OUTCOME_STATUSES
