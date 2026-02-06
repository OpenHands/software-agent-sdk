"""Hooks router for OpenHands Agent Server.

This module defines the HTTP API endpoints for hook operations.
Business logic is delegated to hooks_service.py.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from openhands.agent_server.hooks_service import load_hooks_from_workspace
from openhands.sdk.hooks import HookConfig


hooks_router = APIRouter(prefix="/hooks", tags=["Hooks"])


class HooksRequest(BaseModel):
    """Request body for loading hooks."""

    project_dir: str | None = Field(
        default=None, description="Workspace directory path for project hooks"
    )


class HooksResponse(BaseModel):
    """Response containing hooks configuration."""

    hook_config: HookConfig | None = Field(
        default=None,
        description="Hook configuration loaded from the workspace, or None if not found",  # noqa: E501
    )


@hooks_router.post("", response_model=HooksResponse)
def get_hooks(request: HooksRequest) -> HooksResponse:
    """Load hooks from the workspace .openhands/hooks.json file.

    This endpoint reads the hooks configuration from the project's
    .openhands/hooks.json file if it exists.

    Args:
        request: HooksRequest containing the project directory path.

    Returns:
        HooksResponse containing the hook configuration or None.
    """
    hook_config = load_hooks_from_workspace(project_dir=request.project_dir)
    return HooksResponse(hook_config=hook_config)
