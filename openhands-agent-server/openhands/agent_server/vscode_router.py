"""VSCode router for agent server API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from openhands.agent_server.dependencies import get_vscode_service
from openhands.agent_server.vscode_service import VSCodeService
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

vscode_router = APIRouter(prefix="/vscode", tags=["VSCode"])


class VSCodeUrlResponse(BaseModel):
    """Response model for VSCode URL."""

    url: str | None


@vscode_router.get("/url", response_model=VSCodeUrlResponse)
async def get_vscode_url(
    base_url: str = "http://localhost:8001",
    vscode_service: VSCodeService | None = Depends(get_vscode_service),
) -> VSCodeUrlResponse:
    """Get the VSCode URL with authentication token.

    Args:
        base_url: Base URL for the VSCode server (default: http://localhost:8001)

    Returns:
        VSCode URL with token if available, None otherwise
    """
    if vscode_service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "VSCode is disabled in configuration. Set enable_vscode=true to enable."
            ),
        )

    try:
        url = vscode_service.get_vscode_url(base_url, "workspace")
        return VSCodeUrlResponse(url=url)
    except Exception as e:
        logger.error(f"Error getting VSCode URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to get VSCode URL")


@vscode_router.get("/status")
async def get_vscode_status(
    vscode_service: VSCodeService | None = Depends(get_vscode_service),
) -> dict[str, bool | str]:
    """Get the VSCode server status.

    Returns:
        Dictionary with running status and enabled status
    """
    if vscode_service is None:
        return {
            "running": False,
            "enabled": False,
            "message": "VSCode is disabled in configuration",
        }

    try:
        running = vscode_service.is_running()
        return {"running": running, "enabled": True}
    except Exception as e:
        logger.error(f"Error getting VSCode status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get VSCode status")
