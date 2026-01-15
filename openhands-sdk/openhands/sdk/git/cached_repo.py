"""Git operations for cloning and caching remote repositories.

This module provides utilities for cloning git repositories to a local cache
and keeping them updated. Used by both the skills system and plugin fetching.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from openhands.sdk.git.exceptions import GitCommandError
from openhands.sdk.git.utils import run_git_command
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class GitHelper:
    """Abstraction for git operations, enabling easy mocking in tests.

    This class wraps git commands for cloning, fetching, and managing
    cached repositories. All methods raise GitCommandError on failure.
    """

    def clone(
        self,
        url: str,
        dest: Path,
        depth: int | None = 1,
        branch: str | None = None,
        timeout: int = 120,
    ) -> None:
        """Clone a git repository.

        Args:
            url: Git URL to clone.
            dest: Destination path.
            depth: Clone depth (None for full clone, 1 for shallow).
            branch: Branch/tag to checkout during clone.
            timeout: Timeout in seconds.

        Raises:
            GitCommandError: If clone fails.
        """
        cmd = ["git", "clone"]

        if depth is not None:
            cmd.extend(["--depth", str(depth)])

        if branch:
            cmd.extend(["--branch", branch])

        cmd.extend([url, str(dest)])

        run_git_command(cmd, timeout=timeout)

    def fetch(
        self,
        repo_path: Path,
        remote: str = "origin",
        ref: str | None = None,
        timeout: int = 60,
    ) -> None:
        """Fetch from remote.

        Args:
            repo_path: Path to the repository.
            remote: Remote name.
            ref: Specific ref to fetch (optional).
            timeout: Timeout in seconds.

        Raises:
            GitCommandError: If fetch fails.
        """
        cmd = ["git", "fetch", remote]
        if ref:
            cmd.append(ref)

        run_git_command(cmd, cwd=repo_path, timeout=timeout)

    def checkout(self, repo_path: Path, ref: str, timeout: int = 30) -> None:
        """Checkout a ref (branch, tag, or commit).

        Args:
            repo_path: Path to the repository.
            ref: Branch, tag, or commit to checkout.
            timeout: Timeout in seconds.

        Raises:
            GitCommandError: If checkout fails.
        """
        run_git_command(["git", "checkout", ref], cwd=repo_path, timeout=timeout)

    def reset_hard(self, repo_path: Path, ref: str, timeout: int = 30) -> None:
        """Hard reset to a ref.

        Args:
            repo_path: Path to the repository.
            ref: Ref to reset to (e.g., "origin/main").
            timeout: Timeout in seconds.

        Raises:
            GitCommandError: If reset fails.
        """
        run_git_command(["git", "reset", "--hard", ref], cwd=repo_path, timeout=timeout)

    def get_current_branch(self, repo_path: Path, timeout: int = 10) -> str | None:
        """Get the current branch name.

        Args:
            repo_path: Path to the repository.
            timeout: Timeout in seconds.

        Returns:
            Branch name, or None if in detached HEAD state.

        Raises:
            GitCommandError: If command fails.
        """
        branch = run_git_command(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            timeout=timeout,
        )
        # "HEAD" means detached HEAD state
        return None if branch == "HEAD" else branch


# Default GitHelper instance - can be replaced for testing
_default_git_helper: GitHelper | None = None


def get_git_helper() -> GitHelper:
    """Get the default GitHelper instance."""
    global _default_git_helper
    if _default_git_helper is None:
        _default_git_helper = GitHelper()
    return _default_git_helper


def set_git_helper(helper: GitHelper | None) -> None:
    """Set the default GitHelper instance (for testing)."""
    global _default_git_helper
    _default_git_helper = helper


def cached_clone_or_update(
    url: str,
    repo_path: Path,
    branch: str | None = None,
    update: bool = True,
    git_helper: GitHelper | None = None,
) -> Path | None:
    """Clone or update a git repository in a cache directory.

    This is the main entry point for cached repository operations. It handles:
    - Cloning if the repo doesn't exist
    - Updating (fetch + reset) if it does exist and update=True
    - Using existing cache if update=False

    Args:
        url: Git URL to clone.
        repo_path: Path where the repository should be cached.
        branch: Branch to checkout/update to. If None, uses default branch.
        update: If True and repo exists, update it. If False, use as-is.
        git_helper: GitHelper instance (for testing). Defaults to global instance.

    Returns:
        Path to the local repository if successful, None on failure.
    """
    git = git_helper or get_git_helper()

    try:
        if repo_path.exists() and (repo_path / ".git").exists():
            if update:
                logger.debug(f"Updating repository at {repo_path}")
                _update_repository(repo_path, branch, git)
            else:
                logger.debug(f"Using cached repository at {repo_path}")
        else:
            logger.info(f"Cloning repository from {url}")
            _clone_repository(url, repo_path, branch, git)

        return repo_path

    except GitCommandError as e:
        logger.warning(f"Git operation failed: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error managing repository: {str(e)}")
        return None


def _clone_repository(
    url: str,
    dest: Path,
    branch: str | None,
    git: GitHelper,
) -> None:
    """Clone a git repository.

    Args:
        url: Git URL to clone.
        dest: Destination path.
        branch: Branch to checkout (optional).
        git: GitHelper instance.
    """
    # Remove existing directory if it exists but isn't a valid git repo
    if dest.exists():
        shutil.rmtree(dest)

    git.clone(url, dest, depth=1, branch=branch)
    logger.debug(f"Repository cloned to {dest}")


def _update_repository(
    repo_path: Path,
    branch: str | None,
    git: GitHelper,
) -> None:
    """Update an existing repository.

    Args:
        repo_path: Path to the repository.
        branch: Branch to update to (optional).
        git: GitHelper instance.
    """
    try:
        # Fetch latest changes
        git.fetch(repo_path)

        if branch:
            # Checkout and reset to the specified branch
            _checkout_ref(repo_path, branch, git)
        else:
            # Get the current branch and reset to origin
            current_branch = git.get_current_branch(repo_path)
            if current_branch:
                git.reset_hard(repo_path, f"origin/{current_branch}")

        logger.debug("Repository updated successfully")

    except GitCommandError as e:
        logger.warning(
            f"Failed to update repository: {e}, using existing cached version"
        )


def _checkout_ref(repo_path: Path, ref: str, git: GitHelper) -> None:
    """Checkout a specific ref (branch, tag, or commit).

    Args:
        repo_path: Path to the repository.
        ref: Branch, tag, or commit to checkout.
        git: GitHelper instance.
    """
    logger.debug(f"Checking out ref: {ref}")

    # First try to fetch the ref
    try:
        git.fetch(repo_path, ref=ref)
    except GitCommandError:
        pass  # May fail for commits, that's ok

    # Checkout the ref
    git.checkout(repo_path, ref)

    # If it's a branch, reset to origin
    try:
        git.reset_hard(repo_path, f"origin/{ref}")
    except GitCommandError:
        pass  # May fail for tags/commits, that's ok
