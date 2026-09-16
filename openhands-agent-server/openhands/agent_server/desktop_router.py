"""Desktop router for agent server API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


desktop_router = APIRouter(prefix="/desktop", tags=["Desktop"])


class DesktopUrlResponse(BaseModel):
    """Response model for Desktop URL."""

    url: str | None


@desktop_router.get("/url", response_model=DesktopUrlResponse, deprecated=True)
async def get_desktop_url(
    base_url: str = "http://localhost:8002",  # noqa: ARG001
) -> DesktopUrlResponse:
    """Deprecated since v1.44.1 and scheduled for removal in v1.49.0.

    The VNC/desktop stack has been removed; this always returns 503, the
    same response every deployment has always gotten since VNC defaults off.
    """
    raise HTTPException(
        status_code=503,
        detail="Desktop is disabled. VNC/desktop support has been removed.",
    )
