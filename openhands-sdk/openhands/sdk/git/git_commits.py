"""Commit-history helpers for the display git API: list a repository's
recent commits, and inspect what a single commit changed (file list and
per-file content) entirely from git objects — so files deleted by a
commit, or deleted since, still render.
"""

import logging
import os
from pathlib import Path

from openhands.sdk.git.exceptions import (
    GitCommandError,
    GitPathError,
    GitRepositoryError,
)
from openhands.sdk.git.git_changes import _parse_name_status
from openhands.sdk.git.git_diff import (
    MAX_FILE_SIZE_FOR_GIT_DIFF,
    get_closest_git_repo,
)
from openhands.sdk.git.models import GitChange, GitCommit, GitCommitsPage, GitDiff
from openhands.sdk.git.utils import (
    GIT_EMPTY_TREE_HASH,
    _rev_parse,
    run_git_command,
    validate_git_repository,
)


logger = logging.getLogger(__name__)

DEFAULT_COMMIT_LIMIT = 50

# One line per commit: full sha, short sha, author name, author date
# (strict ISO 8601), subject — unit-separated (0x1F cannot appear in any
# of these fields; subjects cannot contain newlines).
_LOG_FORMAT = "%H%x1f%h%x1f%an%x1f%aI%x1f%s"


def get_git_commits(
    repo_path: str | Path, limit: int = DEFAULT_COMMIT_LIMIT
) -> GitCommitsPage:
    """List a repository's most recent commits (reachable from HEAD),
    newest first.

    Deliberately the branch's plain history — like a Git host's "Commits"
    page — rather than a base-scoped range: committed work must always be
    visible, including in states where a conversation-relative range would
    be empty (e.g. a no-remote repo sitting on its default branch, or a
    fully-pushed branch).

    Args:
        repo_path: Path to the git repository.
        limit: Maximum number of commits to return.

    Returns:
        GitCommitsPage with at most ``limit`` commits and ``has_more``
        telling whether the history holds more.

    Raises:
        GitRepositoryError: If the directory is not a valid git repository.
        GitCommandError: If git commands fail.
    """
    validated_repo = validate_git_repository(repo_path)

    head = _rev_parse(validated_repo, "HEAD")
    if head is None:
        # Unborn/orphan HEAD: `git log HEAD` would fail, and there is
        # nothing committed on this branch to list.
        return GitCommitsPage(commits=[], has_more=False)

    # --no-show-signature: a repo with log.showSignature=true would
    # otherwise interleave GPG output with the formatted lines.
    output = run_git_command(
        [
            "git",
            "--no-pager",
            "log",
            "--no-show-signature",
            f"--format={_LOG_FORMAT}",
            "-n",
            str(limit + 1),
            head,
        ],
        validated_repo,
    )

    commits: list[GitCommit] = []
    for line in output.splitlines() if output else []:
        fields = line.split("\x1f")
        if len(fields) != 5:
            logger.warning(f"Skipping malformed git log line: {line!r}")
            continue
        sha, short_sha, author, timestamp, subject = fields
        commits.append(
            GitCommit(
                sha=sha,
                short_sha=short_sha,
                subject=subject,
                author=author,
                timestamp=timestamp,
            )
        )

    return GitCommitsPage(commits=commits[:limit], has_more=len(commits) > limit)


def _resolve_commit(repo: Path, commit: str) -> str:
    """Resolve ``commit`` to a full commit SHA.

    Raises:
        GitCommandError: If ``commit`` does not name a commit in this
            repository (kept strict so a bad SHA surfaces as an error
            instead of silently rendering "no changes").
    """
    return run_git_command(
        ["git", "--no-pager", "rev-parse", "--verify", f"{commit}^{{commit}}"],
        repo,
    )


def get_commit_changes(repo_path: str | Path, commit: str) -> list[GitChange]:
    """Get the files changed by a single commit, vs its first parent.

    Root commits diff against the empty tree, rendering as all-added.
    Renames follow the shared name-status parsing: DELETED (old path) +
    ADDED (new path).

    Args:
        repo_path: Path to the git repository.
        commit: The commit to inspect (SHA or abbreviation).

    Raises:
        GitRepositoryError: If the directory is not a valid git repository.
        GitCommandError: If ``commit`` does not resolve or git fails.
    """
    validated_repo = validate_git_repository(repo_path)
    sha = _resolve_commit(validated_repo, commit)
    parent = _rev_parse(validated_repo, f"{sha}^") or GIT_EMPTY_TREE_HASH

    output = run_git_command(
        ["git", "--no-pager", "diff", "--name-status", parent, sha],
        validated_repo,
    )
    return _parse_name_status(output.splitlines() if output else [])


def _show_file_at_rev(repo: Path, rev: str, relative_path: Path) -> str:
    """Content of ``relative_path`` at ``rev``; empty string when the file
    does not exist on that side (added/removed relative to the parent).

    Raises:
        GitPathError: If the blob exceeds the diff size cap.
    """
    spec = f"{rev}:{relative_path.as_posix()}"
    try:
        size_output = run_git_command(
            ["git", "--no-pager", "cat-file", "-s", spec], repo
        )
    except GitCommandError:
        return ""

    try:
        size = int(size_output)
    except ValueError:
        size = 0
    if size > MAX_FILE_SIZE_FOR_GIT_DIFF:
        raise GitPathError(
            f"File too large for git diff: {size} bytes "
            f"(max: {MAX_FILE_SIZE_FOR_GIT_DIFF} bytes)"
        )

    try:
        return run_git_command(["git", "--no-pager", "show", spec], repo)
    except GitCommandError:
        return ""


def get_commit_file_diff(file_path: str | Path, commit: str) -> GitDiff:
    """Get the diff of a single file as changed by one commit.

    Both sides come from git objects — never from disk — so files the
    commit deleted (or that were deleted afterwards) still render. There
    is deliberately no on-disk existence check.

    Args:
        file_path: Path to the file, relative to the current working
            directory (absolute paths win the join, mirroring
            ``get_git_diff``).
        commit: The commit to inspect (SHA or abbreviation).

    Returns:
        GitDiff where ``original`` is the file at the commit's first
        parent (empty tree for root commits) and ``modified`` is the file
        at the commit itself.

    Raises:
        GitPathError: If the file is outside the repository or a side
            exceeds the size cap.
        GitRepositoryError: If the path is not inside a git repository.
        GitCommandError: If ``commit`` does not resolve.
    """
    path = Path(os.getcwd(), file_path).resolve()

    closest_git_repo = get_closest_git_repo(path)
    if not closest_git_repo:
        raise GitRepositoryError(f"File is not in a git repository: {path}")
    validated_repo = validate_git_repository(closest_git_repo)

    sha = _resolve_commit(validated_repo, commit)
    parent = _rev_parse(validated_repo, f"{sha}^") or GIT_EMPTY_TREE_HASH

    try:
        relative_path_from_repo = path.relative_to(validated_repo)
    except ValueError as e:
        raise GitPathError(f"File is not within git repository: {path}") from e

    original = _show_file_at_rev(validated_repo, parent, relative_path_from_repo)
    modified = _show_file_at_rev(validated_repo, sha, relative_path_from_repo)

    logger.info(f"Generated commit file diff for {path} at {sha}")
    return GitDiff(modified=modified, original=original)
