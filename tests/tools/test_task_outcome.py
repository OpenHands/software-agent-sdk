from openhands.sdk.tool.spec import Tool
from openhands.tools.preset import TaskOutcome


def test_task_outcome_accepts_outcome_summary_alias():
    outcome = TaskOutcome(
        status="blocked",
        outcome_summary=(
            "Could not continue without a token. Blocker: missing_secret. "
            "User action required: configure the API token."
        ),
    )

    assert outcome.summary == (
        "Could not continue without a token. Blocker: missing_secret. "
        "User action required: configure the API token."
    )


def test_task_outcome_finish_tool_schema_uses_outcome_summary():
    schema = Tool(
        name="FinishTool", params={"response_schema": TaskOutcome}
    ).model_dump(mode="json")["params"]["response_schema"]

    properties = schema["properties"]
    assert set(properties) == {"status", "outcome_summary"}
    assert properties["outcome_summary"]["description"] == (
        "Concise outcome summary. Include what was completed, blockers, "
        "required user action, and relevant next steps."
    )
    assert properties["status"]["description"] == (
        "Agent's semantic assessment of task completion."
    )
