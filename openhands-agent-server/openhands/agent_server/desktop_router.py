"""Desktop router for agent server API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from openhands.agent_server.dependencies import (
    get_desktop_service as get_desktop_service_di,
)
from openhands.agent_server.desktop_service import (
    DesktopService,
    get_desktop_service as _legacy_get_desktop_service,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)
# Back-compat: expose module-level get_desktop_service symbol for tests
get_desktop_service = _legacy_get_desktop_service


desktop_router = APIRouter(prefix="/desktop", tags=["Desktop"])


class DesktopUrlResponse(BaseModel):
    """Response model for Desktop URL."""

    url: str | None


@desktop_router.get("/url", response_model=DesktopUrlResponse)
async def get_desktop_url(
    base_url: str = "http://localhost:8002",
    desktop_service: DesktopService | None = Depends(get_desktop_service_di),
) -> DesktopUrlResponse:
    """Get the noVNC URL for desktop access.

    Args:
        base_url: Base URL for the noVNC server (default: http://localhost:8002)

        If called via FastAPI, this router uses DI to obtain the service.
        When called directly in tests, we fallback to module-level accessor.

    Returns:
        noVNC URL if available, None otherwise
    """
    if desktop_service is None or not hasattr(desktop_service, "get_vnc_url"):
        # Fallback for direct function invocation in tests
        # Use module-level symbol (re-exported) so tests can patch it
        desktop_service = get_desktop_service()
        if desktop_service is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Desktop is disabled in configuration. "
                    "Set enable_vnc=true to enable."
                ),
            )

    try:
        url = desktop_service.get_vnc_url(base_url)
        return DesktopUrlResponse(url=url)
    except Exception as e:
        logger.error(f"Error getting desktop URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to get desktop URL")
