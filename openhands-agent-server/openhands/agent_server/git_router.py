"""Git router for OpenHands SDK."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter

from openhands.sdk.git.git_changes import get_git_changes
from openhands.sdk.git.git_diff import get_git_diff
from openhands.sdk.git.models import GitChange, GitDiff


git_router = APIRouter(prefix="/git", tags=["Git"])
logger = logging.getLogger(__name__)


@git_router.get("/changes/{path:path}")
async def git_changes(
    path: Path,
) -> list[GitChange]:
    loop = asyncio.get_running_loop()
    changes = await loop.run_in_executor(None, get_git_changes, path)
    return changes


# bash event routes
@git_router.get("/diff/{path:path}")
async def git_diff(
    path: Path,
) -> GitDiff:
    loop = asyncio.get_running_loop()
    changes = await loop.run_in_executor(None, get_git_diff, path)
    return changes
