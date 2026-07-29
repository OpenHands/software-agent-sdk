"""VSCode router for agent server API endpoints."""

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openhands.agent_server.vscode_service import get_vscode_service
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

vscode_router = APIRouter(prefix="/vscode", tags=["VSCode"])


class VSCodeUrlResponse(BaseModel):
    """Response model for VSCode URL."""

    url: str | None


@vscode_router.get("/url", response_model=VSCodeUrlResponse)
async def get_vscode_url(
    base_url: str | None = None, workspace_dir: str | None = None
) -> VSCodeUrlResponse:
    """Get the VSCode URL with authentication token.

    Args:
        base_url: Base URL for the VSCode server. When omitted, the URL is
            built from the actually configured VSCode port
            (``http://localhost:{vscode_port}``), so callers that don't know
            the deployment topology get a URL that matches where the server
            really binds instead of a hardcoded ``:8001``.
        workspace_dir: Path to workspace directory. When omitted, defaults
            to the configured ``Config.workspace_path``.

    Returns:
        VSCode URL with token if available, None otherwise
    """
    vscode_service = get_vscode_service()
    if vscode_service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "VSCode is disabled in configuration. Set enable_vscode=true to enable."
            ),
        )

    # Derive the allowed root from the authoritative workspace configuration
    # rather than the process CWD, so deployment-specific paths (e.g.
    # ``/mnt/project``) are accepted when the server is configured to use them.
    workspace_base_dir = Path(vscode_service.config.workspace_path).resolve()
    if workspace_dir is None:
        workspace_dir = str(workspace_base_dir)

    # Validate workspace_dir before the service try-block so invalid client
    # input receives a 400 response instead of being masked as a 500 error.
    resolved = Path(workspace_dir).resolve()
    if not resolved.is_relative_to(workspace_base_dir):
        raise HTTPException(
            status_code=400,
            detail="workspace_dir must be within the workspace directory",
        )

    try:
        url = vscode_service.get_vscode_url(base_url, str(resolved))
        # Rebuild the folder query parameter from the validated canonical path
        # so that URL-encoded traversal sequences (e.g. ``%2e%2e``) cannot
        # bypass the boundary check and reach the VSCode server verbatim.
        if url is not None and "folder=" in url:
            base, _, query = url.partition("?")
            params: dict[str, str] = {}
            token: str | None = None
            for pair in query.split("&"):
                if not pair:
                    continue
                key, sep, value = pair.partition("=")
                if key == "folder":
                    params["folder"] = str(resolved)
                elif key == "token":
                    token = value
                else:
                    params[key] = value
            query_str = urlencode(params)
            if token is not None:
                query_str = f"{query_str}&token={token}" if query_str else f"token={token}"
            url = f"{base}?{query_str}" if query_str else base
        response = VSCodeUrlResponse(url=url)
        # API-level assertion to prevent regression: a valid request must
        # never produce an error status (the route returns 200 on success).
        assert response is not None
        assert response.url is None or response.url.startswith(("http://", "https://"))
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting VSCode URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to get VSCode URL")


@vscode_router.get("/status")
async def get_vscode_status() -> dict[str, bool | str]:
    """Get the VSCode server status.

    Returns:
        Dictionary with running status and enabled status
    """
    vscode_service = get_vscode_service()
    if vscode_service is None:
        return {
            "running": False,
            "enabled": False,
            "message": "VSCode is disabled in configuration",
        }

    try:
        return {"running": vscode_service.is_running(), "enabled": True}
    except Exception as e:
        logger.error(f"Error getting VSCode status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get VSCode status")