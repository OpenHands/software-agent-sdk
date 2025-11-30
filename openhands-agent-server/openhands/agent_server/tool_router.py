"""Tool router for OpenHands SDK."""

from fastapi import APIRouter

from openhands.sdk.tool.registry import list_registered_tools
from openhands.tools.preset.default import register_default_tools


tool_router = APIRouter(prefix="/tools", tags=["Tools"])
# Register default tools for backward compatibility
# Planning tools and other custom tools are now dynamically registered
# when creating a RemoteConversation
register_default_tools(enable_browser=True)


# Tool listing
@tool_router.get("/")
async def list_available_tools() -> list[str]:
    """List all available tools."""
    tools = list_registered_tools()
    return tools
