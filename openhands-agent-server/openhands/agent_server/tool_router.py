"""Tool router for OpenHands SDK."""

from fastapi import APIRouter

from openhands.sdk.tool.registry import list_registered_tools
from openhands.tools.preset.default import register_default_tools
from openhands.tools.preset.gemini import register_gemini_tools
from openhands.tools.preset.planning import register_planning_tools


tool_router = APIRouter(prefix="/tools", tags=["Tools"])
register_default_tools(enable_browser=True)
register_gemini_tools(enable_browser=True)
register_planning_tools()


# Tool listing
@tool_router.get("/")
async def list_available_tools() -> list[str]:
    """List all available tools."""
    tools = list_registered_tools()
    return tools
