from openhands.sdk.context.skills import Skill, TaskTrigger
from openhands.sdk.context.skills.types import InputMetadata


def test_task_skill_prompt_appending():
    """Test that Skill with TaskTrigger correctly appends missing variables prompt."""
    # Create Skill with TaskTrigger and variables in content
    task_skill = Skill(
        name="test-task",
        content="Task with ${variable1} and ${variable2}",
        source="test.md",
        trigger=TaskTrigger(triggers=["task"]),
    )

    # Check that the prompt was appended
    expected_prompt = (
        "\n\nIf the user didn't provide any of these variables, ask the user to "
        "provide them first before the agent can proceed with the task."
    )
    assert expected_prompt in task_skill.content

    # Create Skill with TaskTrigger without variables but with inputs
    task_skill_with_inputs = Skill(
        name="test-task-inputs",
        content="Task without variables",
        source="test.md",
        trigger=TaskTrigger(triggers=["task"]),
        inputs=[InputMetadata(name="input1", description="Test input")],
    )

    # Check that the prompt was appended
    assert expected_prompt in task_skill_with_inputs.content

    # Create Skill with TaskTrigger without variables or inputs
    task_skill_no_vars = Skill(
        name="test-task-no-vars",
        content="Task without variables or inputs",
        source="test.md",
        trigger=TaskTrigger(triggers=["task"]),
    )

    # Check that the prompt was NOT appended
    assert expected_prompt not in task_skill_no_vars.content
