"""Tool router for OpenHands SDK."""

from fastapi import APIRouter
from pydantic import BaseModel

from openhands.sdk.tool.registry import (
    ToolCatalogEntry,
    list_registered_tools,
    list_tool_catalog,
)
from openhands.tools.preset.default import (
    register_builtins_agents,
    register_default_tools,
)
from openhands.tools.preset.gemini import register_gemini_tools
from openhands.tools.preset.planning import register_planning_tools


tool_router = APIRouter(prefix="/tools", tags=["Tools"])
register_default_tools(enable_browser=True)
register_builtins_agents(enable_browser=True)
register_gemini_tools(enable_browser=True)
register_planning_tools()


# Tool listing
@tool_router.get("/")
async def list_available_tools() -> list[str]:
    """List all available tools."""
    tools = list_registered_tools()
    return tools


class ToolCatalogResponse(BaseModel):
    tools: list[ToolCatalogEntry]


@tool_router.get("/catalog")
async def get_tool_catalog() -> ToolCatalogResponse:
    """List the tools this server offers for configuring an agent.

    Clients offer the ``user_selectable`` entries; ``usable`` says whether this
    server's runtime can run the tool.
    """
    return ToolCatalogResponse(tools=list_tool_catalog())
