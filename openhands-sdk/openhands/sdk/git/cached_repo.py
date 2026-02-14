"""Git operations for cloning and caching remote repositories.

This module provides utilities for cloning git repositories to a local cache
and keeping them updated. Used by both the skills system and plugin fetching.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from filelock import FileLock, Timeout

from openhands.sdk.git.exceptions import GitCommandError
from openhands.sdk.git.utils import run_git_command
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Default timeout for acquiring cache locks (seconds)
# Consistent with other lock timeouts in the SDK (io/local.py, event_store.py)
DEFAULT_LOCK_TIMEOUT = 30


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
            depth: Clone depth (None for full clone, 1 for shallow). Note that
                shallow clones only fetch the tip of the specified branch. If you
                later need to checkout a specific commit that isn't the branch tip,
                the checkout may fail. Use depth=None for full clones if you need
                to checkout arbitrary commits.
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

    def get_default_branch(self, repo_path: Path, timeout: int = 10) -> str | None:
        """Get the default branch name from the remote.

        Queries origin/HEAD to determine the remote's default branch. This is set
        during clone and points to the branch that would be checked out by default.

        Args:
            repo_path: Path to the repository.
            timeout: Timeout in seconds.

        Returns:
            Default branch name (e.g., "main" or "master"), or None if it cannot
            be determined (e.g., origin/HEAD is not set).

        Raises:
            GitCommandError: If the git command itself fails (not if ref is missing).
        """
        try:
            # origin/HEAD is a symbolic ref pointing to the default branch
            ref = run_git_command(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                cwd=repo_path,
                timeout=timeout,
            )
            # Output is like "refs/remotes/origin/main" - extract branch name
            prefix = "refs/remotes/origin/"
            if ref.startswith(prefix):
                return ref[len(prefix) :]
            return None
        except GitCommandError:
            # origin/HEAD may not be set (e.g., bare clone, or never configured)
            return None

    def get_head_commit(self, repo_path: Path, timeout: int = 10) -> str:
        """Get the current HEAD commit SHA.

        Args:
            repo_path: Path to the repository.
            timeout: Timeout in seconds.

        Returns:
            Full 40-character commit SHA of HEAD.

        Raises:
            GitCommandError: If command fails.
        """
        return run_git_command(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            timeout=timeout,
        )


def try_cached_clone_or_update(
    url: str,
    repo_path: Path,
    ref: str | None = None,
    update: bool = True,
    git_helper: GitHelper | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> Path | None:
    """Clone or update a git repository in a cache directory.

    This is the main entry point for cached repository operations.

    Behavior:
        - If repo doesn't exist: clone (shallow, --depth 1) with optional ref
        - If repo exists and update=True: fetch, checkout+reset to ref
        - If repo exists and update=False with ref: checkout ref without fetching
        - If repo exists and update=False without ref: use as-is

    The update sequence is: fetch origin -> checkout ref -> reset --hard origin/ref.
    This ensures local changes are discarded and the cache matches the remote.

    Concurrency:
        Uses file-based locking to prevent race conditions when multiple processes
        access the same cache directory. The lock file is created adjacent to the
        repo directory (repo_path.lock).

    Args:
        url: Git URL to clone.
        repo_path: Path where the repository should be cached.
        ref: Branch, tag, or commit to checkout. If None, uses default branch.
        update: If True and repo exists, fetch and update it. If False, skip fetch.
        git_helper: GitHelper instance for git operations. If None, creates one.
        lock_timeout: Timeout in seconds for acquiring the lock. Default is 5 minutes.

    Returns:
        Path to the local repository if successful, None on failure.
        Returns None (not raises) on git errors to allow graceful degradation.
    """
    git = git_helper if git_helper is not None else GitHelper()

    # Ensure parent directory exists for both the repo and lock file
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    # Use a lock file adjacent to the repo directory
    lock_path = repo_path.with_suffix(".lock")
    lock = FileLock(lock_path)

    try:
        with lock.acquire(timeout=lock_timeout):
            return _do_clone_or_update(url, repo_path, ref, update, git)
    except Timeout:
        logger.warning(
            f"Timed out waiting for lock on {repo_path} after {lock_timeout}s"
        )
        return None
    except GitCommandError as e:
        logger.warning(f"Git operation failed: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error managing repository: {str(e)}")
        return None


def _do_clone_or_update(
    url: str,
    repo_path: Path,
    ref: str | None,
    update: bool,
    git: GitHelper,
) -> Path:
    """Perform the actual clone or update operation (called while holding lock).

    Args:
        url: Git URL to clone.
        repo_path: Path where the repository should be cached.
        ref: Branch, tag, or commit to checkout.
        update: Whether to update existing repos.
        git: GitHelper instance.

    Returns:
        Path to the repository.

    Raises:
        GitCommandError: If git operations fail.
    """
    if repo_path.exists() and (repo_path / ".git").exists():
        if update:
            logger.debug(f"Updating repository at {repo_path}")
            _update_repository(repo_path, ref, git)
        elif ref:
            logger.debug(f"Checking out ref {ref} at {repo_path}")
            _checkout_ref(repo_path, ref, git)
        else:
            logger.debug(f"Using cached repository at {repo_path}")
    else:
        logger.info(f"Cloning repository from {url}")
        _clone_repository(url, repo_path, ref, git)

    return repo_path


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
    ref: str | None,
    git: GitHelper,
) -> None:
    """Update an existing cached repository to the latest remote state.

    Fetches from origin and resets to match the remote. On any failure, logs a
    warning and returns silently—the cached repository remains usable (just
    potentially stale).

    Behavior by scenario:
        1. ref is specified: Checkout and reset to that ref (branch/tag/commit)
        2. ref is None, on a branch: Reset to origin/{current_branch}
        3. ref is None, detached HEAD: Checkout the remote's default branch
           (determined via origin/HEAD), then reset to origin/{default_branch}.
           This handles the case where a previous fetch with a specific ref
           (e.g., a tag) left the repo in detached HEAD state.

    The detached HEAD recovery ensures that calling fetch(source, update=True)
    without a ref always updates to "the latest", even if a previous call used
    a specific tag or commit. Without this, the repo would be stuck on the old
    ref with no way to get back to the default branch.

    Args:
        repo_path: Path to the repository.
        ref: Branch, tag, or commit to update to. If None, uses current branch
            or falls back to the remote's default branch.
        git: GitHelper instance.
    """
    # Fetch from origin - if this fails, we still have a usable (stale) cache
    if not _try_fetch(repo_path, git):
        return

    # If a specific ref was requested, check it out
    if ref:
        _try_checkout_and_reset(repo_path, ref, git)
        return

    # No ref specified - update based on current state
    current_branch = git.get_current_branch(repo_path)

    if current_branch:
        # On a branch: reset to track origin
        _try_reset_to_origin(repo_path, current_branch, git)
        return

    # Detached HEAD: recover by checking out the default branch
    _recover_from_detached_head(repo_path, git)


def _try_fetch(repo_path: Path, git: GitHelper) -> bool:
    """Attempt to fetch from origin. Returns True on success, False on failure."""
    try:
        git.fetch(repo_path)
        return True
    except GitCommandError as e:
        logger.warning(f"Failed to fetch updates: {e}. Using cached version.")
        return False


def _try_checkout_and_reset(repo_path: Path, ref: str, git: GitHelper) -> None:
    """Attempt to checkout and reset to a specific ref. Logs warning on failure."""
    try:
        _checkout_ref(repo_path, ref, git)
        logger.debug(f"Repository updated to {ref}")
    except GitCommandError as e:
        logger.warning(f"Failed to checkout {ref}: {e}. Using cached version.")


def _try_reset_to_origin(repo_path: Path, branch: str, git: GitHelper) -> None:
    """Attempt to reset to origin/{branch}. Logs warning on failure."""
    try:
        git.reset_hard(repo_path, f"origin/{branch}")
        logger.debug("Repository updated successfully")
    except GitCommandError as e:
        logger.warning(
            f"Failed to reset to origin/{branch}: {e}. Using cached version."
        )


def _recover_from_detached_head(repo_path: Path, git: GitHelper) -> None:
    """Recover from detached HEAD state by checking out the default branch.

    This handles the scenario where:
    1. User previously fetched with ref="v1.0.0" (a tag) -> repo is in detached HEAD
    2. User now fetches with update=True but no ref -> expects "latest"

    Without this recovery, the repo would stay stuck on the old tag. By checking
    out the default branch, we ensure update=True without a ref means "latest
    from the default branch".
    """
    default_branch = git.get_default_branch(repo_path)

    if not default_branch:
        logger.warning(
            "Repository is in detached HEAD state and default branch could not be "
            "determined. Specify a ref explicitly to update, or the cached version "
            "will be used as-is."
        )
        return

    logger.debug(
        f"Repository in detached HEAD state, "
        f"checking out default branch: {default_branch}"
    )

    try:
        git.checkout(repo_path, default_branch)
        git.reset_hard(repo_path, f"origin/{default_branch}")
        logger.debug(f"Repository updated to default branch: {default_branch}")
    except GitCommandError as e:
        logger.warning(
            f"Failed to checkout default branch {default_branch}: {e}. "
            "Using cached version."
        )


def _checkout_ref(repo_path: Path, ref: str, git: GitHelper) -> None:
    """Checkout a specific ref (branch, tag, or commit).

    Handles each ref type with appropriate semantics:

    - **Branches**: Checks out the branch and resets to ``origin/{branch}`` to
      ensure the local branch matches the remote state.

    - **Tags**: Checks out in detached HEAD state. Tags are immutable, so no
      reset is performed.

    - **Commits**: Checks out in detached HEAD state. For shallow clones, the
      commit must be reachable from fetched history.

    Args:
        repo_path: Path to the repository.
        ref: Branch name, tag name, or commit SHA to checkout.
        git: GitHelper instance.

    Raises:
        GitCommandError: If checkout fails (ref doesn't exist or isn't reachable).
    """
    logger.debug(f"Checking out ref: {ref}")

    # Checkout is the critical operation - let it raise if it fails
    git.checkout(repo_path, ref)

    # Determine what we checked out by examining HEAD state
    current_branch = git.get_current_branch(repo_path)

    if current_branch is None:
        # Detached HEAD means we checked out a tag or commit - nothing more to do
        logger.debug(f"Checked out {ref} (detached HEAD - tag or commit)")
        return

    # We're on a branch - reset to sync with origin
    try:
        git.reset_hard(repo_path, f"origin/{current_branch}")
        logger.debug(f"Branch {current_branch} reset to origin/{current_branch}")
    except GitCommandError:
        # Branch may not exist on origin (e.g., local-only branch)
        logger.debug(
            f"Could not reset to origin/{current_branch} "
            f"(branch may not exist on remote)"
        )
