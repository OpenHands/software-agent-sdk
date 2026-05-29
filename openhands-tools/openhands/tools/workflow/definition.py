"""Dynamic workflow tool definitions."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState
    from openhands.tools.workflow.impl import WorkflowExecutor


class WorkflowAction(Action):
    """Schema for running a Python dynamic workflow script."""

    name: str = Field(description="A short name for this workflow run.")
    script: str = Field(
        description=(
            "Python workflow script to run. It must define `async def main(wf):` "
            "and coordinate work only through the provided `wf` object."
        )
    )
    max_concurrency: int = Field(
        default=8,
        ge=1,
        le=64,
        description="Maximum number of sub-agent tasks to run concurrently.",
    )


class WorkflowObservation(Observation):
    """Observation from a dynamic workflow run."""

    name: str = Field(description="The workflow name that was executed.")
    status: Literal["completed", "error"] = Field(
        description="The workflow execution status."
    )


_WORKFLOW_DESCRIPTION = """Run a dynamic workflow written as Python orchestration code.

Use this tool for large tasks that benefit from parallel sub-agents, such as
codebase-wide audits, independent plan reviews, security sweeps, or discovery
work where intermediate results should stay outside the main conversation.

Provide a Python script that defines exactly this entry point:

```python
async def main(wf):
    ...
```

The script coordinates sub-agents through the `wf` object. It should not read or
write files, run shell commands, or perform the engineering work directly.
Sub-agents should do that work through their normal OpenHands tools and security
policy. Scripts should use only the documented `wf` methods; private `wf`
attributes are rejected. Large reducer inputs may be truncated before being sent
to the reducer sub-agent.

Available `wf` methods:
- `await wf.run_agent(...)`
- `await wf.map_agents(...)`
- `await wf.reduce_agent(...)`
- `wf.flatten(values)` — flatten one level of nesting (not recursive)

`map_agents` accepts either a callable prompt, such as
`lambda item: f"Review this finding: {item}"`, or a string template containing
`{item}`.

Example:
```python
async def main(wf):
    strategies = ["minimal fix", "test-first", "security-focused"]
    plans = await wf.map_agents(
        items=strategies,
        subagent_type="general-purpose",
        max_concurrency=3,
        prompt=lambda strategy: f"Create a plan using this strategy: {strategy}",
    )
    critiques = await wf.map_agents(
        items=plans,
        subagent_type="code-reviewer",
        prompt=lambda plan: f"Adversarially critique this plan: {plan}",
    )
    return await wf.reduce_agent(
        items={"plans": plans, "critiques": critiques},
        prompt="Synthesize the safest and simplest final plan.",
    )
```

This MVP executes generated Python in-process after best-effort validation. Treat
running a workflow as approving generated code execution.
"""


class WorkflowTool(ToolDefinition[WorkflowAction, WorkflowObservation]):
    """Tool for running a dynamic Python workflow."""

    @classmethod
    def create(
        cls,
        executor: "WorkflowExecutor",
        description: str = _WORKFLOW_DESCRIPTION,
    ) -> Sequence["WorkflowTool"]:
        return [
            cls(
                action_type=WorkflowAction,
                observation_type=WorkflowObservation,
                description=description,
                annotations=ToolAnnotations(
                    title="workflow",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


class WorkflowToolSet(ToolDefinition[WorkflowAction, WorkflowObservation]):
    """Tool set that creates the dynamic workflow tool."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",  # noqa: ARG003
    ) -> Sequence[ToolDefinition]:
        from openhands.tools.workflow.impl import WorkflowExecutor

        return WorkflowTool.create(executor=WorkflowExecutor())


register_tool(WorkflowToolSet.name, WorkflowToolSet)
register_tool(WorkflowTool.name, WorkflowTool)
