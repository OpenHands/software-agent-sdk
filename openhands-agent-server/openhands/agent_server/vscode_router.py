"""VSCode router for agent server API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openhands.agent_server.vscode_service import get_vscode_service
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

vscode_router = APIRouter(prefix="/vscode", tags=["VSCode"])


class VSCodeUrlResponse(BaseModel):
    """Response model for VSCode URL."""

    url: str | None


@vscode_router.get("/url", response_model=VSCodeUrlResponse, deprecated=True)
async def get_vscode_url(
    base_url: str | None = None, workspace_dir: str = "workspace"
) -> VSCodeUrlResponse:
    """Get the VSCode URL with authentication token.

    Deprecated since v1.50.1 and scheduled for removal in v1.55.0.
    Built-in OpenVSCode has been removed from default Agent Server images;
    use the standalone VSCode App extension instead.

    Args:
        base_url: Base URL for the VSCode server. When omitted, the URL is
            built from the actually configured VSCode port
            (``http://localhost:{vscode_port}``), so callers that don't know
            the deployment topology get a URL that matches where the server
            really binds instead of a hardcoded ``:8001``.
        workspace_dir: Path to workspace directory

    Returns:
        VSCode URL with token if available, None otherwise
    """
    vscode_service = get_vscode_service()
    if vscode_service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Built-in OpenVSCode is deprecated and disabled in configuration. "
                "Use the standalone VSCode App extension instead."
            ),
        )

    try:
        if not vscode_service.is_running():
            raise HTTPException(
                status_code=503,
                detail=(
                    "VSCode server is not running. "
                    "Built-in OpenVSCode is deprecated; "
                    "use the standalone VSCode App extension instead."
                ),
            )
        url = vscode_service.get_vscode_url(base_url, workspace_dir)
        return VSCodeUrlResponse(url=url)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting VSCode URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to get VSCode URL")


@vscode_router.get("/status", deprecated=True)
async def get_vscode_status() -> dict[str, bool | str]:
    """Get the VSCode server status.

    Deprecated since v1.50.1 and scheduled for removal in v1.55.0.
    Built-in OpenVSCode has been removed from default Agent Server images;
    use the standalone VSCode App extension instead.

    Returns:
        Dictionary with running status and enabled status
    """
    vscode_service = get_vscode_service()
    if vscode_service is None:
        return {
            "running": False,
            "enabled": False,
            "message": (
                "Built-in OpenVSCode is deprecated and disabled in configuration. "
                "Use the standalone VSCode App extension instead."
            ),
        }

    try:
        return {
            "running": vscode_service.is_running(),
            "enabled": True,
            "message": (
                "Built-in OpenVSCode is deprecated. "
                "Use the standalone VSCode App extension instead."
            ),
        }
    except Exception as e:
        logger.error(f"Error getting VSCode status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get VSCode status")
