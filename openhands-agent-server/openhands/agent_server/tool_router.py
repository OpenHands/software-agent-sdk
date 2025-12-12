"""Tool router for OpenHands SDK."""

from fastapi import APIRouter

from openhands.sdk.tool.registry import list_registered_tools


tool_router = APIRouter(prefix="/tools", tags=["Tools"])
# All tools are now dynamically registered when creating a RemoteConversation
# The client sends tool_module_qualnames which the server imports to trigger
# tool auto-registration


# Tool listing
@tool_router.get("/")
async def list_available_tools() -> list[str]:
    """List all available tools."""
    tools = list_registered_tools()
    return tools
