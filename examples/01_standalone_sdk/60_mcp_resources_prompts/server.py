"""Example MCP server advertising a resource, a resource template, and a prompt.

Run by ``main.py`` over stdio; not meant to be executed directly.
"""

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field


mcp = FastMCP("kanban")


@mcp.resource(
    "docs://usage",
    name="usage-guide",
    description="How to drive this kanban server.",
    mime_type="text/plain",
)
def usage_guide() -> str:
    return (
        "Kanban usage: create a card with a title, move it through the "
        "columns backlog -> doing -> review -> done, and archive finished "
        "cards weekly."
    )


@mcp.resource(
    "docs://projects/{project_id}",
    name="project-notes",
    description="Working agreement for one project.",
    mime_type="text/plain",
)
def project_notes(project_id: str) -> str:
    return (
        f"Project {project_id}: keep cards under two days in 'doing', "
        "review every pull request against the checklist, and never skip "
        "the demo."
    )


@mcp.prompt(
    name="plan_task",
    description="Plan one task under a project using the project notes.",
)
def plan_task(
    task: Annotated[str, Field(description="The task to plan.")],
    project: Annotated[str, Field(description="Owning project.")] = "inbox",
) -> str:
    return (
        f"Plan the task '{task}' for project '{project}'. Read the project "
        "notes first, break the task into steps small enough to finish in "
        "one day, and name the column each step starts in."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
