"""Desktop router for agent server API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openhands.agent_server.desktop_service import get_desktop_service
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

desktop_router = APIRouter(prefix="/desktop", tags=["Desktop"])


class DesktopUrlResponse(BaseModel):
    """Response model for Desktop URL."""

    url: str | None


@desktop_router.get("/url", response_model=DesktopUrlResponse)
async def get_desktop_url(
    base_url: str = "http://localhost:8002",
) -> DesktopUrlResponse:
    """Get the noVNC URL for desktop access.

    Args:
        base_url: Base URL for the noVNC server (default: http://localhost:8002)

    Returns:
        noVNC URL if available, None otherwise
    """
    desktop_service = get_desktop_service()
    if desktop_service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Desktop is disabled in configuration. Set enable_vnc=true to enable."
            ),
        )

    try:
        url = desktop_service.get_vnc_url(base_url)
        return DesktopUrlResponse(url=url)
    except Exception as e:
        logger.error(f"Error getting desktop URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to get desktop URL")
