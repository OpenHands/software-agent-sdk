#!/usr/bin/env python3
"""Get git diff in a single git file for the closest git repo in the file system"""

import json
import logging
import os
import sys
from pathlib import Path

from openhands.sdk.git.exceptions import (
    GitCommandError,
    GitPathError,
    GitRepositoryError,
)
from openhands.sdk.git.models import GitDiff
from openhands.sdk.git.utils import (
    get_valid_ref,
    run_git_command,
    validate_git_repository,
)


logger = logging.getLogger(__name__)


MAX_FILE_SIZE_FOR_GIT_DIFF = 1024 * 1024  # 1 Mb


def get_closest_git_repo(path: Path) -> Path | None:
    """Find the closest git repository by walking up the directory tree.

    Args:
        path: Starting path to search from

    Returns:
        Path to the git repository root, or None if not found
    """
    current_path = path.resolve()

    while True:
        git_path = current_path / ".git"
        if git_path.exists():  # Could be file (worktree) or directory
            logger.debug(f"Found git repository at: {current_path}")
            return current_path

        parent = current_path.parent
        if parent == current_path:  # Reached filesystem root
            logger.debug(f"No git repository found for path: {path}")
            return None
        current_path = parent


def _base_file_size(repo: Path, ref: str, relative_path: Path) -> int | None:
    """Size of the file (blob) ``relative_path`` at ``ref``, or ``None``
    when ``ref`` has no file at that path."""
    spec = f"{ref}:{relative_path.as_posix()}"
    try:
        object_type = run_git_command(
            ["git", "cat-file", "-t", spec], repo, expected_failure=True
        )
        if object_type != "blob":
            return None
        return int(run_git_command(["git", "cat-file", "-s", spec], repo))
    except (GitCommandError, ValueError):
        return None


def get_git_diff(relative_file_path: str | Path, ref: str | None = None) -> GitDiff:
    """Get git diff for a single file.

    Args:
        relative_file_path: Path to the file relative to current working directory
        ref: Optional explicit ref to compare against (e.g. ``"HEAD"`` or a
            commit hash). When ``None``, compares against the auto-detected
            upstream/default branch as before.

    Returns:
        GitDiff object containing diff information

    Raises:
        GitPathError: If file is too large, or exists neither on disk nor
            at the base ref (a working-tree deletion diffs as the base text
            against ``""``)
        GitRepositoryError: If not in a git repository
        GitCommandError: If git commands fail (including when ``ref`` is
            provided but does not resolve in the repository).
    """
    path = Path(os.getcwd(), relative_file_path).resolve()
    missing_error = GitPathError(f"File does not exist: {path}")

    # A path missing from disk may still be a working-tree deletion (or the
    # old side of a rename) that only exists at the base ref.
    deleted = not path.exists()

    # Check file size
    try:
        file_size = 0 if deleted else os.path.getsize(path)
        if file_size > MAX_FILE_SIZE_FOR_GIT_DIFF:
            raise GitPathError(
                f"File too large for git diff: {file_size} bytes "
                f"(max: {MAX_FILE_SIZE_FOR_GIT_DIFF} bytes)"
            )
    except OSError as e:
        raise GitPathError(f"Cannot access file: {path}") from e

    # Find git repository
    closest_git_repo = get_closest_git_repo(path)
    if not closest_git_repo:
        if deleted:
            raise missing_error
        raise GitRepositoryError(f"File is not in a git repository: {path}")

    # Validate the git repository
    validated_repo = validate_git_repository(closest_git_repo)

    # Must match get_changes_in_repo's base selection (purpose="display"),
    # so the per-file diff lines up with the change list it was opened from.
    current_rev = get_valid_ref(validated_repo, override=ref, purpose="display")
    if not current_rev:
        if deleted:
            raise missing_error
        logger.warning(f"No valid git reference found for {validated_repo}")
        return GitDiff(modified="", original="")

    # Get the relative path from the git repo root
    try:
        relative_path_from_repo = path.relative_to(validated_repo)
    except ValueError as e:
        raise GitPathError(f"File is not within git repository: {path}") from e

    if deleted:
        base_size = _base_file_size(
            validated_repo, current_rev, relative_path_from_repo
        )
        if base_size is None:
            raise missing_error
        if base_size > MAX_FILE_SIZE_FOR_GIT_DIFF:
            raise GitPathError(
                f"File too large for git diff: {base_size} bytes "
                f"(max: {MAX_FILE_SIZE_FOR_GIT_DIFF} bytes)"
            )

    # Get old content (from the ref)
    try:
        # Read the blob verbatim and normalize it like the modified side below,
        # so an unchanged file keeps its edge whitespace on both sides.
        original = "\n".join(
            run_git_command(
                ["git", "show", f"{current_rev}:{relative_path_from_repo}"],
                validated_repo,
                strip=False,
            ).splitlines()
        )
    except GitCommandError:
        logger.debug(f"No old content found for {path} at ref {current_rev}")
        original = ""

    if deleted:
        logger.info(f"Generated git diff for deleted file {path}")
        return GitDiff(modified="", original=original)

    # Get new content (current file)
    try:
        with open(path, encoding="utf-8") as f:
            modified = "\n".join(f.read().splitlines())
    except (OSError, UnicodeDecodeError) as e:
        logger.error(f"Failed to read file {path}: {e}")
        modified = ""

    logger.info(f"Generated git diff for {path}")
    return GitDiff(
        modified=modified,
        original=original,
    )


if __name__ == "__main__":
    diff = get_git_diff(sys.argv[-1])
    print(json.dumps(diff))
