#!/usr/bin/env python3
"""Get git changes in the current working directory relative to the remote origin
if possible.
"""

import glob
import json
import logging
import os
from pathlib import Path

from openhands.sdk.git.exceptions import GitCommandError, GitError
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


def _parse_name_status(name_status_output: str) -> list[GitChange]:
    """Parse ``git diff --name-status -z`` output into GitChange objects.

    The ``-z`` form is NUL-delimited and emits paths verbatim (no quoting
    or C-style escapes), so names containing whitespace or non-ASCII
    characters survive intact. Renames are split into DELETED (old path) +
    ADDED (new path); copies surface only the new path as ADDED.
    """
    # Fields run status\0path\0, or status\0old\0new\0 for renames/copies.
    # Empty fields (e.g. from the trailing terminator) are not real fields.
    fields = [field for field in name_status_output.split("\x00") if field]
    changes: list[GitChange] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1

        # Rename/copy operations carry the old and new path.
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                logger.error(f"Rename/copy status without both paths: {status}")
                raise GitCommandError(
                    message=f"Unexpected git diff output format: {status}",
                    command=["git", "diff", "--name-status", "-z"],
                    exit_code=0,
                    stderr="Invalid output format",
                )
            old_path, new_path = fields[index], fields[index + 1]
            index += 2
            if status.startswith("R"):
                changes.append(
                    GitChange(
                        status=GitChangeStatus.DELETED,
                        path=Path(old_path),
                    )
                )
                logger.debug(f"Found git rename: {old_path} -> {new_path}")
            else:
                logger.debug(f"Found git copy: -> {new_path}")
            changes.append(
                GitChange(
                    status=GitChangeStatus.ADDED,
                    path=Path(new_path),
                )
            )
            continue

        if index >= len(fields):
            logger.error(f"Status without a path: {status}")
            raise GitCommandError(
                message=f"Unexpected git diff output format: {status}",
                command=["git", "diff", "--name-status", "-z"],
                exit_code=0,
                stderr="Invalid output format",
            )
        path = fields[index]
        index += 1

        if status == "??":
            status = "A"
        elif status == "*":
            status = "M"

        # Check for valid single-character status codes
        if status in {"M", "A", "D", "U"}:
            changes.append(
                GitChange(
                    status=_map_git_status_to_enum(status),
                    path=Path(path),
                )
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

    # Get changed files using secure git command (-z keeps path bytes
    # verbatim: no quote/escape munging, no whitespace ambiguity).
    try:
        changed_files_output = run_git_command(
            ["git", "--no-pager", "diff", "--name-status", "-z", ref],
            validated_repo,
        )
    except GitCommandError as e:
        logger.error(f"Failed to get git diff for {validated_repo}: {e}")
        raise
    changes = _parse_name_status(changed_files_output)

    # Get untracked files
    try:
        untracked_output = run_git_command(
            [
                "git",
                "--no-pager",
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            validated_repo,
        )
    except GitCommandError as e:
        logger.error(f"Failed to get untracked files for {validated_repo}: {e}")
        untracked_output = ""
    for path in untracked_output.split("\x00"):
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

    # First try the workspace directory
    changes = get_changes_in_repo(cwd, ref=ref)

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
    for git_dir in git_dirs:
        try:
            git_dir_changes = get_changes_in_repo(str(Path(cwd, git_dir)), ref=ref)
        except GitError:
            logger.warning(
                f"Skipping nested git directory {git_dir}: not a valid repository"
            )
            continue
        for change in git_dir_changes:
            # Create a new GitChange with the updated path
            updated_change = GitChange(
                status=change.status,
                path=Path(git_dir) / change.path,
            )
            changes.append(updated_change)

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
