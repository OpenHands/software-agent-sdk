"""Hooks router for OpenHands Agent Server.

This module defines the HTTP API endpoints for hook operations.
Business logic is delegated to hooks_service.py.
"""

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from openhands.agent_server.hooks_service import load_hooks
from openhands.sdk.hooks import HookConfig


hooks_router = APIRouter(prefix="/hooks", tags=["Hooks"])


def _validate_project_dir(project_dir: str) -> bool:
    return Path(project_dir).is_absolute()


class HooksRequest(BaseModel):
    """Request body for loading hooks."""

    load_project: bool = Field(
        default=True,
        description=(
            "Whether to load project hooks from {project_dir}/.openhands/hooks.json"
        ),
    )
    load_user: bool = Field(
        default=False,
        description="Whether to load user hooks from ~/.openhands/hooks.json",
    )
    project_dir: str | None = Field(
        default=None, description="Workspace directory path for project hooks"
    )


class HooksResponse(BaseModel):
    """Response containing hooks configuration."""

    hook_config: HookConfig | None = Field(
        default=None,
        description=(
            "Hook configuration loaded from the workspace, or None if not found"
        ),
    )


@hooks_router.post("", response_model=HooksResponse)
def get_hooks(request: HooksRequest) -> HooksResponse:
    """Load hooks from the workspace .openhands/hooks.json file."""

    hook_config = None
    if request.project_dir is not None:
        if not _validate_project_dir(request.project_dir):
            return HooksResponse(hook_config=None)

        hook_config = load_hooks(
            load_project=request.load_project,
            load_user=request.load_user,
            project_dir=request.project_dir,
        )
    else:
        hook_config = load_hooks(
            load_project=request.load_project,
            load_user=request.load_user,
            project_dir=None,
        )
    return HooksResponse(hook_config=hook_config)
