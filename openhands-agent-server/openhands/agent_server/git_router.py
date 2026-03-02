"""Git router for OpenHands SDK."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Query

from openhands.agent_server.server_details_router import update_last_execution_time
from openhands.sdk.git.git_changes import get_git_changes
from openhands.sdk.git.git_diff import get_git_diff
from openhands.sdk.git.models import GitChange, GitDiff
from openhands.sdk.utils.deprecation import deprecated


git_router = APIRouter(prefix="/git", tags=["Git"])
logger = logging.getLogger(__name__)


async def _get_git_changes(path: str) -> list[GitChange]:
    """Internal helper to get git changes for a given path."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_git_changes, Path(path))


async def _get_git_diff(path: str) -> GitDiff:
    """Internal helper to get git diff for a given path."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_git_diff, Path(path))


@git_router.get("/changes")
async def git_changes_query(
    path: str = Query(..., description="The git repository path"),
) -> list[GitChange]:
    """Get git changes using query parameter (preferred method)."""
    return await _get_git_changes(path)


@git_router.get("/changes/{path:path}")
@deprecated(
    deprecated_in="1.15.0",
    removed_in="1.20.0",
    details=(
        "Use the /git/changes endpoint with a query parameter for the path "
        "instead of a path parameter. This allows for better handling of "
        "complex paths and is more consistent with other endpoints."
    ),
)
async def git_changes_path(path: str) -> list[GitChange]:
    """Get git changes using path parameter (legacy, for backwards compatibility)."""
    return await _get_git_changes(path)


@git_router.get("/diff")
async def git_diff_query(
    path: str = Query(..., description="The file path to get diff for"),
) -> GitDiff:
    """Get git diff using query parameter (preferred method)."""
    return await _get_git_diff(path)


@git_router.get("/diff/{path:path}")
@deprecated(
    deprecated_in="1.15.0",
    removed_in="1.20.0",
    details=(
        "Use the /git/diff endpoint with a query parameter for the path "
        "instead of a path parameter. This allows for better handling of "
        "complex paths and is more consistent with other endpoints."
    ),
)
async def git_diff_path(path: str) -> GitDiff:
    """Get git diff using path parameter (legacy, for backwards compatibility)."""
    return await _get_git_diff(path)
