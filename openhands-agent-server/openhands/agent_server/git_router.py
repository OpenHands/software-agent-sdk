"""Git router for OpenHands SDK."""

import asyncio
import functools
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Path as PathParam, Query

from openhands.agent_server.server_details_router import update_last_execution_time
from openhands.sdk.git.exceptions import GitError, GitRepositoryError
from openhands.sdk.git.git_changes import get_git_changes
from openhands.sdk.git.git_commits import (
    get_commit_changes,
    get_commit_file_diff,
    get_git_commits,
)
from openhands.sdk.git.git_diff import get_git_diff
from openhands.sdk.git.models import GitChange, GitCommitsPage, GitDiff


git_router = APIRouter(prefix="/git", tags=["Git"])
logger = logging.getLogger(__name__)


_REF_QUERY_DESCRIPTION = (
    "Optional git ref to diff against (e.g. 'HEAD' for git status-style "
    "changes, or a commit hash). When omitted, the upstream/default branch "
    "is auto-detected."
)

_COMMIT_QUERY_DESCRIPTION = (
    "Optional commit SHA. When set, the diff is the change that commit "
    "introduced (vs its first parent), read entirely from git objects so "
    "deleted files still render. Mutually exclusive with 'ref'."
)

# Hex-only so a path/query value can never reach git argv as an option
# (list-args protect against shell injection, not option injection).
_SHA_PATTERN = r"^[0-9a-fA-F]{4,64}$"


async def _get_git_changes(path: str, ref: str | None) -> list[GitChange]:
    """Internal helper to get git changes for a given path."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, functools.partial(get_git_changes, Path(path), ref=ref)
        )
    except GitRepositoryError:
        # A non-repo workspace has no git changes to report; respond with an
        # empty list so the Changes tab can render normally instead of 500ing.
        logger.debug("Path %s is not a git repository; returning no changes", path)
        return []


async def _get_git_diff(path: str, ref: str | None) -> GitDiff:
    """Internal helper to get git diff for a given path."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, functools.partial(get_git_diff, Path(path), ref=ref)
        )
    except GitRepositoryError:
        # Only collapse the not-a-repo case to an empty diff; file-level
        # GitPathError (missing/oversize/outside-repo) stays a 500 so
        # callers can distinguish it from "no changes".
        logger.debug("Path %s is not in a git repository; returning empty diff", path)
        return GitDiff(modified=None, original=None)


async def _get_git_commits(path: str, limit: int) -> GitCommitsPage:
    """Internal helper to list commits for a given repo path."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, functools.partial(get_git_commits, Path(path), limit=limit)
        )
    except GitRepositoryError:
        # A non-repo workspace has no commits to report; respond with an
        # empty page so the commits section can render its empty state.
        logger.debug("Path %s is not a git repository; returning no commits", path)
        return GitCommitsPage(commits=[], has_more=False)


async def _get_commit_changes(path: str, sha: str) -> list[GitChange]:
    """Internal helper to get the files changed by one commit."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, functools.partial(get_commit_changes, Path(path), sha)
        )
    except GitRepositoryError:
        logger.debug("Path %s is not a git repository; returning no changes", path)
        return []


async def _get_commit_file_diff(path: str, commit: str) -> GitDiff:
    """Internal helper to get one file's diff as changed by one commit."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, functools.partial(get_commit_file_diff, Path(path), commit)
        )
    except GitRepositoryError:
        logger.debug("Path %s is not in a git repository; returning empty diff", path)
        return GitDiff(modified=None, original=None)


@git_router.get("/changes")
async def git_changes_query(
    path: str = Query(..., description="The git repository path"),
    ref: str | None = Query(None, description=_REF_QUERY_DESCRIPTION),
) -> list[GitChange]:
    """Get git changes using query parameter (preferred method)."""
    try:
        return await _get_git_changes(path, ref)
    except GitError as e:
        # GitRepositoryError is already handled in the helper (returns []).
        # Any remaining GitError subclass (e.g. GitCommandError) surfaces as
        # 400 so the client can show an actionable error instead of an
        # opaque 500.
        raise HTTPException(status_code=400, detail=str(e))


@git_router.get("/diff")
async def git_diff_query(
    path: str = Query(..., description="The file path to get diff for"),
    ref: str | None = Query(None, description=_REF_QUERY_DESCRIPTION),
    commit: str | None = Query(
        None, pattern=_SHA_PATTERN, description=_COMMIT_QUERY_DESCRIPTION
    ),
) -> GitDiff:
    """Get git diff using query parameter (preferred method)."""
    if ref is not None and commit is not None:
        raise HTTPException(
            status_code=400, detail="'ref' and 'commit' are mutually exclusive"
        )
    try:
        if commit is not None:
            return await _get_commit_file_diff(path, commit)
        return await _get_git_diff(path, ref)
    except GitError as e:
        # GitRepositoryError is already handled in the helpers (returns an
        # empty diff). Any remaining GitError subclass (e.g. GitCommandError,
        # GitPathError) surfaces as 400 so the client can show an actionable
        # error instead of an opaque 500.
        raise HTTPException(status_code=400, detail=str(e))


@git_router.get("/commits")
async def git_commits_query(
    path: str = Query(..., description="The git repository path"),
    limit: int = Query(50, ge=1, le=200, description="Maximum commits to return"),
) -> GitCommitsPage:
    """List the repository's most recent commits, newest first."""
    try:
        return await _get_git_commits(path, limit)
    except GitError as e:
        # GitRepositoryError is already handled in the helper (returns an
        # empty page). Any remaining GitError subclass surfaces as 400.
        raise HTTPException(status_code=400, detail=str(e))


@git_router.get("/commits/{sha}/changes")
async def git_commit_changes_query(
    sha: str = PathParam(..., pattern=_SHA_PATTERN, description="Commit SHA"),
    path: str = Query(..., description="The git repository path"),
) -> list[GitChange]:
    """Get the files changed by a single commit (vs its first parent)."""
    try:
        return await _get_commit_changes(path, sha)
    except GitError as e:
        # GitRepositoryError is already handled in the helper (returns []).
        # An unknown/unresolvable sha raises GitCommandError -> 400.
        raise HTTPException(status_code=400, detail=str(e))
