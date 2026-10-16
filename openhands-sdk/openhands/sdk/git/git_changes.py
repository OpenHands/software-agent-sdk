#!/usr/bin/env python3
"""Get git changes in the current working directory relative to the remote origin
if possible.
"""

import glob
import json
import logging
import os
from pathlib import Path

from openhands.sdk.git.exceptions import (
    GitCommandError,
    GitError,
    GitRepositoryError,
)
from openhands.sdk.git.models import GitChange, GitChangeStatus
from openhands.sdk.git.utils import (
    get_valid_ref,
    run_git_command,
    validate_git_repository,
)


logger = logging.getLogger(__name__)


def _map_git_status_to_enum(status: str) -> GitChangeStatus:
    """Map git status codes to GitChangeStatus enum values."""
    status_mapping = {
        "M": GitChangeStatus.UPDATED,
        "A": GitChangeStatus.ADDED,
        "D": GitChangeStatus.DELETED,
        "U": GitChangeStatus.UPDATED,  # Unmerged files are treated as updated
    }
    if status not in status_mapping:
        raise ValueError(f"Unknown git status: {status}")
    return status_mapping[status]


def _parse_name_status(output: str) -> list[GitChange]:
    """Parse ``git diff --name-status -z`` output into GitChange objects.

    With ``-z`` git prints every field verbatim (no C-quoting of spaces,
    non-ASCII characters, tabs or newlines) and ends each one with a NUL:
    the status, then one path, or two (old, new) for renames and copies.

    Renames are split into DELETED (old path) + ADDED (new path); copies
    surface only the new path as ADDED.
    """
    fields = output.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes: list[GitChange] = []
    i = 0
    while i < len(fields):
        status = fields[i]
        path_count = 2 if status[:1] in {"R", "C"} else 1
        paths = fields[i + 1 : i + 1 + path_count]
        if not status or len(paths) != path_count or not all(paths):
            logger.error(f"Unexpected git diff output format: {output!r}")
            raise GitCommandError(
                message=f"Unexpected git diff output format: {output!r}",
                command=["git", "diff", "--name-status", "-z"],
                exit_code=0,
                stderr="Invalid output format",
            )
        i += 1 + path_count

        if status.startswith("R"):
            # Rename: convert to delete (old path) + add (new path)
            old_path, new_path = paths
            changes.append(
                GitChange(status=GitChangeStatus.DELETED, path=Path(old_path))
            )
            changes.append(GitChange(status=GitChangeStatus.ADDED, path=Path(new_path)))
            logger.debug(f"Found git rename: {old_path} -> {new_path}")
            continue
        if status.startswith("C"):
            # Copy: only add the new path (original remains)
            new_path = paths[1]
            changes.append(GitChange(status=GitChangeStatus.ADDED, path=Path(new_path)))
            logger.debug(f"Found git copy: -> {new_path}")
            continue

        path = paths[0]
        # Check for valid single-character status codes
        if status in {"M", "A", "D", "U"}:
            changes.append(
                GitChange(status=_map_git_status_to_enum(status), path=Path(path))
            )
            logger.debug(f"Found git change: {status} {path}")
        else:
            logger.error(f"Unexpected git status '{status}' for file {path}")
            raise GitCommandError(
                message=f"Unexpected git status: {status}",
                command=["git", "diff", "--name-status", "-z"],
                exit_code=0,
                stderr=f"Unexpected status code: {status}",
            )
    return changes


def get_changes_in_repo(
    repo_dir: str | Path, ref: str | None = None
) -> list[GitChange]:
    """Get git changes in a repository relative to a reference.

    By default, compares against the auto-detected remote branch. Pass
    ``ref="HEAD"`` to get ``git status``-style diffs (working tree + index
    vs the latest commit) instead.

    Args:
        repo_dir: Path to the git repository
        ref: Optional explicit ref to compare against (e.g. ``"HEAD"`` or a
            commit hash). When ``None``, behaves as before and compares
            against the upstream/default branch.

    Returns:
        List of GitChange objects representing the changes

    Raises:
        GitRepositoryError: If the directory is not a valid git repository
        GitCommandError: If git commands fail (including when ``ref`` is
            provided but does not resolve in the repository).
    """
    # Validate the repository first
    validated_repo = validate_git_repository(repo_dir)

    # These changes are rendered to the user (e.g. the GUI's Diff view), so
    # auto-detect the base with the display policy: committed and even
    # pushed work stays visible instead of vanishing behind a vacuous base.
    ref = get_valid_ref(validated_repo, override=ref, purpose="display")
    if not ref:
        logger.warning(f"No valid git reference found for {validated_repo}")
        return []

    # Get changed files using secure git command
    # ``-z`` makes git print names verbatim and NUL-terminated, so names
    # with spaces, non-ASCII characters, tabs or newlines survive parsing.
    try:
        changed_files_output = run_git_command(
            ["git", "--no-pager", "diff", "--name-status", "-z", ref],
            validated_repo,
            strip=False,
        )
    except GitCommandError as e:
        logger.error(f"Failed to get git diff for {validated_repo}: {e}")
        raise
    changes = _parse_name_status(changed_files_output)

    # Get untracked files
    try:
        untracked_output = run_git_command(
            ["git", "--no-pager", "ls-files", "-z", "--others", "--exclude-standard"],
            validated_repo,
            strip=False,
        )
        untracked_files = untracked_output.split("\0")
    except GitCommandError as e:
        logger.error(f"Failed to get untracked files for {validated_repo}: {e}")
        untracked_files = []
    for path in untracked_files:
        if path:
            changes.append(
                GitChange(
                    status=GitChangeStatus.ADDED,
                    path=Path(path),
                )
            )
            logger.debug(f"Found untracked file: {path}")

    logger.info(f"Found {len(changes)} total git changes in {validated_repo}")
    return changes


def get_git_changes(cwd: str | Path, ref: str | None = None) -> list[GitChange]:
    git_dirs = {
        os.path.dirname(f)[2:]
        for f in glob.glob("./*/.git", root_dir=cwd, recursive=True)
    }

    # First try the workspace directory. A directory that is not a repository
    # itself but holds repositories below it (the ``{base}/{repo}`` layout of a
    # repository-backed conversation) contributes no changes of its own.
    base_error: GitRepositoryError | None = None
    try:
        changes = get_changes_in_repo(cwd, ref=ref)
    except GitRepositoryError as e:
        if not git_dirs:
            raise
        base_error = e
        changes = []

    # Filter out any changes which are inside one of the nested repositories.
    # This compares path ancestry rather than string prefixes: a nested
    # repository named "foo" must not swallow a root-repository change to
    # "foobar/file.py" or "foo-config.yaml", which merely share its name as a
    # prefix.
    changes = [
        change
        for change in changes
        if not any(Path(change.path).is_relative_to(git_dir) for git_dir in git_dirs)
    ]

    # Add changes from git directories
    found_nested_repo = False
    for git_dir in git_dirs:
        try:
            git_dir_changes = get_changes_in_repo(str(Path(cwd, git_dir)), ref=ref)
        except GitError:
            logger.warning(
                f"Skipping nested git directory {git_dir}: not a valid repository"
            )
            continue
        found_nested_repo = True
        for change in git_dir_changes:
            # Create a new GitChange with the updated path
            updated_change = GitChange(
                status=change.status,
                path=Path(git_dir) / change.path,
            )
            changes.append(updated_change)

    # Neither the directory nor anything below it is a repository.
    if base_error is not None and not found_nested_repo:
        raise base_error

    changes.sort(key=lambda change: str(change.path))

    return changes


if __name__ == "__main__":
    try:
        changes = get_git_changes(os.getcwd())
        # Convert GitChange objects to dictionaries for JSON serialization
        changes_dict = [
            {
                "status": change.status.value,
                "path": str(change.path),
            }
            for change in changes
        ]
        print(json.dumps(changes_dict))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
