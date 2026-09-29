"""Tool router for OpenHands SDK."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from openhands.agent_server.profile_launch import (
    can_probe_tools,
    configured_browser_available,
)
from openhands.sdk.tool import BROWSER_TOOL_NAME
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
async def get_tool_catalog(request: Request) -> ToolCatalogResponse:
    """List the tools this server offers for configuring an agent.

    Clients offer the ``user_selectable`` entries; ``usable`` says whether the
    runtime conversations run in can run the tool.
    """
    config = getattr(request.app.state, "config", None)
    if config is None:
        return ToolCatalogResponse(tools=list_tool_catalog())
    entries = list_tool_catalog(check_usable=can_probe_tools(config))
    browser = configured_browser_available(config)
    if browser is not None:
        entries = [
            entry.model_copy(update={"usable": browser})
            if entry.name == BROWSER_TOOL_NAME
            else entry
            for entry in entries
        ]
    return ToolCatalogResponse(tools=entries)
