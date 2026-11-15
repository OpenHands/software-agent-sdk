"""Hooks router for OpenHands Agent Server.

This module defines the HTTP API endpoints for hook operations.
Business logic is delegated to hooks_service.py.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from openhands.agent_server.hooks_service import load_hooks
from openhands.sdk.hooks import HookConfig


hooks_router = APIRouter(prefix="/hooks", tags=["Hooks"])


class HooksRequest(BaseModel):
    """Request body for loading hooks."""

    project_dir: str | None = Field(
        default=None, description="Workspace directory path for project hooks"
    )
    load_project_hooks: bool = Field(
        default=True,
        description="Load project hooks from {project_dir}/.openhands/hooks.json",
    )
    load_user_hooks: bool = Field(
        default=True, description="Load user hooks from ~/.openhands/hooks.json"
    )


class HooksResponse(BaseModel):
    """Response containing hooks configuration."""

    hook_config: HookConfig | None = Field(
        default=None,
        description=(
            "Project and user hook configuration, merged, or None if neither has hooks"
        ),
    )


@hooks_router.post("", response_model=HooksResponse)
def get_hooks(request: HooksRequest) -> HooksResponse:
    """Load project and user hooks.

    Reads the project's ``.openhands/hooks.json`` and the user's
    ``~/.openhands/hooks.json`` (each when enabled) and merges them, project
    handlers first.

    Args:
        request: HooksRequest with the project directory and the sources to load.

    Returns:
        HooksResponse containing the hook configuration or None.
    """
    hook_config = load_hooks(
        project_dir=request.project_dir,
        load_project_hooks=request.load_project_hooks,
        load_user_hooks=request.load_user_hooks,
    )
    return HooksResponse(hook_config=hook_config)
