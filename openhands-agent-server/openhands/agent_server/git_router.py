"""Git router for OpenHands SDK."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Query

from openhands.agent_server.server_details_router import update_last_execution_time
from openhands.sdk.git.exceptions import GitRepositoryError
from openhands.sdk.git.git_changes import get_git_changes
from openhands.sdk.git.git_diff import get_git_diff
from openhands.sdk.git.models import GitChange, GitDiff


git_router = APIRouter(prefix="/git", tags=["Git"])
logger = logging.getLogger(__name__)


async def _get_git_changes(path: str) -> list[GitChange]:
    """Internal helper to get git changes for a given path."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, get_git_changes, Path(path))
    except GitRepositoryError:
        # A non-repo workspace has no git changes to report; respond with an
        # empty list so the Changes tab can render normally instead of 500ing.
        logger.debug("Path %s is not a git repository; returning no changes", path)
        return []


async def _get_git_diff(path: str) -> GitDiff:
    """Internal helper to get git diff for a given path."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, get_git_diff, Path(path))
    except GitRepositoryError:
        # Only collapse the not-a-repo case to an empty diff; file-level
        # GitPathError (missing/oversize/outside-repo) stays a 500 so
        # callers can distinguish it from "no changes".
        logger.debug("Path %s is not in a git repository; returning empty diff", path)
        return GitDiff(modified=None, original=None)


@git_router.get("/changes")
async def git_changes_query(
    path: str = Query(..., description="The git repository path"),
) -> list[GitChange]:
    """Get git changes using query parameter (preferred method)."""
    return await _get_git_changes(path)


@git_router.get("/diff")
async def git_diff_query(
    path: str = Query(..., description="The file path to get diff for"),
) -> GitDiff:
    """Get git diff using query parameter (preferred method)."""
    return await _get_git_diff(path)
