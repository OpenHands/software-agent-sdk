"""Run the stdio MCP test server advertising resources, templates and prompts."""

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field


mcp = FastMCP("resources-prompts-server")


@mcp.resource(
    "docs://usage",
    name="usage-guide",
    description="How to drive this kanban server.",
    mime_type="text/plain",
)
def usage_guide() -> str:
    """Static usage guide resource."""
    return "Create a card, then move it through the columns."


@mcp.resource(
    "docs://projects/{project_id}",
    name="project-notes",
    description="Notes for one project.",
    mime_type="text/plain",
)
def project_notes(project_id: str) -> str:
    """Template resource resolved per project."""
    return f"Notes for project {project_id}."


@mcp.resource(
    "images://logo",
    name="logo",
    description="Binary logo image.",
    mime_type="image/png",
)
def logo() -> bytes:
    """Binary blob resource."""
    return b"\x89PNG\r\n\x1a\nlogo-bytes"


@mcp.prompt(
    name="plan_task",
    description="Plan work on one task.",
)
def plan_task(
    task: Annotated[str, Field(description="The task to plan.")],
    project: Annotated[str, Field(description="Owning project.")] = "inbox",
) -> str:
    """Render a planning prompt."""
    return f"Plan the task '{task}' under project '{project}'."


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
