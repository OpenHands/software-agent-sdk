"""Tests for git_commits.py using temporary repositories and bash commands."""

import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from openhands.sdk.git.exceptions import GitCommandError, GitPathError
from openhands.sdk.git.git_commits import (
    get_commit_changes,
    get_commit_file_diff,
    get_git_commits,
)
from openhands.sdk.git.git_diff import MAX_FILE_SIZE_FOR_GIT_DIFF
from openhands.sdk.git.models import GitChangeStatus


def run_bash_command(command: str, cwd: str) -> subprocess.CompletedProcess:
    """Run a bash command in the specified directory."""
    return subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def setup_git_repo(repo_dir: str) -> None:
    """Initialize a git repository with basic configuration."""
    run_bash_command("git init -b main", repo_dir)
    run_bash_command("git config user.name 'Test User'", repo_dir)
    run_bash_command("git config user.email 'test@example.com'", repo_dir)


def commit_file(repo_dir: str, name: str, content: str, message: str) -> str:
    """Write ``name``, commit it with ``message``, and return the commit SHA."""
    (Path(repo_dir) / name).write_text(content)
    run_bash_command("git add .", repo_dir)
    run_bash_command(f"git commit -m '{message}'", repo_dir)
    return run_bash_command("git rev-parse HEAD", repo_dir).stdout.strip()


def test_get_git_commits_lists_full_history_newest_first():
    """The list is the branch's plain recent history — commits stay visible
    regardless of push/base state (the original complaint was committed
    work disappearing)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        setup_git_repo(temp_dir)
        commit_file(temp_dir, "a.txt", "one", "first commit")
        commit_file(temp_dir, "a.txt", "two", "second commit")

        # Act
        page = get_git_commits(temp_dir)

        # Assert — newest first, nothing scoped away, nothing truncated.
        assert [commit.subject for commit in page.commits] == [
            "second commit",
            "first commit",
        ]
        assert page.has_more is False


def test_get_git_commits_populates_commit_fields():
    """Each commit carries the metadata the GUI renders (sha, short sha,
    subject, author, ISO timestamp)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        setup_git_repo(temp_dir)
        sha = commit_file(temp_dir, "a.txt", "one", "add a")

        # Act
        commit = get_git_commits(temp_dir).commits[0]

        # Assert
        assert commit.sha == sha
        assert sha.startswith(commit.short_sha)
        assert commit.subject == "add a"
        assert commit.author == "Test User"
        assert datetime.fromisoformat(commit.timestamp) is not None


def test_get_git_commits_caps_at_limit_with_has_more():
    """A history longer than ``limit`` is capped and flagged."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        setup_git_repo(temp_dir)
        commit_file(temp_dir, "a.txt", "one", "first commit")
        commit_file(temp_dir, "a.txt", "two", "second commit")

        # Act
        page = get_git_commits(temp_dir, limit=1)

        # Assert
        assert [commit.subject for commit in page.commits] == ["second commit"]
        assert page.has_more is True


def test_get_git_commits_empty_repo_returns_empty_page():
    """A freshly init'd repo (unborn HEAD) yields an empty page, not an
    error."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        setup_git_repo(temp_dir)

        # Act
        page = get_git_commits(temp_dir)

        # Assert
        assert page.commits == []
        assert page.has_more is False


def test_get_commit_changes_reports_the_commits_own_files():
    """A commit's change list has each file with its real status — and only
    the files that commit touched, not the whole working tree."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange — base commit, then one commit that edits, adds and deletes.
        setup_git_repo(temp_dir)
        (Path(temp_dir) / "to_modify.txt").write_text("original")
        (Path(temp_dir) / "to_delete.txt").write_text("doomed")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'base'", temp_dir)

        (Path(temp_dir) / "to_modify.txt").write_text("changed")
        (Path(temp_dir) / "added.txt").write_text("new")
        run_bash_command("git rm -q to_delete.txt", temp_dir)
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'mixed'", temp_dir)
        sha = run_bash_command("git rev-parse HEAD", temp_dir).stdout.strip()

        # Act
        changes = get_commit_changes(temp_dir, sha)

        # Assert
        changes_by_path = {str(change.path): change.status for change in changes}
        assert changes_by_path == {
            "to_modify.txt": GitChangeStatus.UPDATED,
            "added.txt": GitChangeStatus.ADDED,
            "to_delete.txt": GitChangeStatus.DELETED,
        }


def test_get_commit_changes_root_commit_lists_all_files_as_added():
    """A root commit has no parent, so it diffs against the empty tree."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        setup_git_repo(temp_dir)
        (Path(temp_dir) / "a.txt").write_text("a")
        (Path(temp_dir) / "b.txt").write_text("b")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'root'", temp_dir)
        sha = run_bash_command("git rev-parse HEAD", temp_dir).stdout.strip()

        # Act
        changes = get_commit_changes(temp_dir, sha)

        # Assert
        assert {str(change.path) for change in changes} == {"a.txt", "b.txt"}
        assert all(change.status == GitChangeStatus.ADDED for change in changes)


def test_get_commit_changes_unknown_sha_raises():
    """An unresolvable commit surfaces as an error, not as 'no changes'."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        setup_git_repo(temp_dir)
        commit_file(temp_dir, "a.txt", "a", "base")

        # Act + Assert
        with pytest.raises(GitCommandError):
            get_commit_changes(temp_dir, "deadbeefdeadbeef")


def test_get_commit_file_diff_returns_parent_and_commit_content():
    """The diff sides are the file at the commit's parent vs at the commit."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        setup_git_repo(temp_dir)
        commit_file(temp_dir, "file.txt", "before", "base")
        sha = commit_file(temp_dir, "file.txt", "after", "edit")
        # A later working-tree edit must not leak into the commit's diff.
        (Path(temp_dir) / "file.txt").write_text("working tree noise")

        # Act
        diff = get_commit_file_diff(Path(temp_dir) / "file.txt", sha)

        # Assert
        assert diff.original == "before"
        assert diff.modified == "after"


def test_get_commit_file_diff_renders_deleted_file_from_git_objects():
    """A file deleted by a commit no longer exists on disk, but its diff
    must still render (original = parent content, modified = empty)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        setup_git_repo(temp_dir)
        commit_file(temp_dir, "doomed.txt", "contents", "base")
        run_bash_command("git rm -q doomed.txt", temp_dir)
        run_bash_command("git commit -m 'delete'", temp_dir)
        sha = run_bash_command("git rev-parse HEAD", temp_dir).stdout.strip()

        # Act
        diff = get_commit_file_diff(Path(temp_dir) / "doomed.txt", sha)

        # Assert
        assert diff.original == "contents"
        assert diff.modified == ""


def test_get_commit_file_diff_rejects_oversize_blob():
    """The size cap applies to the git blob (there is no on-disk file to
    measure for deleted files)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        setup_git_repo(temp_dir)
        big = "x" * (MAX_FILE_SIZE_FOR_GIT_DIFF + 1)
        sha = commit_file(temp_dir, "big.txt", big, "big file")

        # Act + Assert
        with pytest.raises(GitPathError):
            get_commit_file_diff(Path(temp_dir) / "big.txt", sha)
