"""Pytest configuration for Git tool tests."""

import tempfile
from pathlib import Path

import pytest

from openhands.sdk.git.utils import run_git_command


@pytest.fixture
def temp_git_repo(tmp_path):
    """Create a temporary git repository for testing.

    This fixture creates a git repository with basic configuration
    (user name and email) to allow committing without errors.
    """
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    run_git_command(["git", "init"], cwd=repo_path)
    run_git_command(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
    )
    run_git_command(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
    )

    return repo_path


@pytest.fixture
def temp_git_repo_with_commits(temp_git_repo):
    """Create a temporary git repository with some commits.

    This fixture extends temp_git_repo by adding a few commits
    to make testing branch operations and history easier.
    """
    # Create and commit first file
    file1 = temp_git_repo / "file1.txt"
    file1.write_text("First file content")
    run_git_command(["git", "add", "."], cwd=temp_git_repo)
    run_git_command(
        ["git", "commit", "-m", "First commit"],
        cwd=temp_git_repo,
    )

    # Create and commit second file
    file2 = temp_git_repo / "file2.txt"
    file2.write_text("Second file content")
    run_git_command(["git", "add", "."], cwd=temp_git_repo)
    run_git_command(
        ["git", "commit", "-m", "Second commit"],
        cwd=temp_git_repo,
    )

    return temp_git_repo
