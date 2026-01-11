"""Git operations helper for plugin fetching."""

from __future__ import annotations

import subprocess
from pathlib import Path

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class GitError(Exception):
    """Raised when a git operation fails."""

    pass


class GitHelper:
    """Abstraction for git operations, enabling easy mocking in tests."""

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
            depth: Clone depth (None for full clone).
            branch: Branch/tag to checkout.
            timeout: Timeout in seconds.

        Raises:
            GitError: If clone fails.
        """
        cmd = ["git", "clone"]

        if depth is not None:
            cmd.extend(["--depth", str(depth)])

        if branch:
            cmd.extend(["--branch", branch])

        cmd.extend([url, str(dest)])

        logger.debug(f"Running: {' '.join(cmd)}")

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            raise GitError(f"Clone failed: {stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise GitError(f"Clone timed out after {timeout}s") from e

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
            GitError: If fetch fails.
        """
        cmd = ["git", "fetch", remote]
        if ref:
            cmd.append(ref)

        logger.debug(f"Running: {' '.join(cmd)} in {repo_path}")

        try:
            subprocess.run(
                cmd,
                cwd=repo_path,
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            raise GitError(f"Fetch failed: {stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise GitError(f"Fetch timed out after {timeout}s") from e

    def checkout(self, repo_path: Path, ref: str, timeout: int = 30) -> None:
        """Checkout a ref (branch, tag, or commit).

        Args:
            repo_path: Path to the repository.
            ref: Branch, tag, or commit to checkout.
            timeout: Timeout in seconds.

        Raises:
            GitError: If checkout fails.
        """
        cmd = ["git", "checkout", ref]

        logger.debug(f"Running: {' '.join(cmd)} in {repo_path}")

        try:
            subprocess.run(
                cmd,
                cwd=repo_path,
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            raise GitError(f"Checkout failed: {stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise GitError(f"Checkout timed out after {timeout}s") from e

    def reset_hard(self, repo_path: Path, ref: str, timeout: int = 30) -> None:
        """Hard reset to a ref.

        Args:
            repo_path: Path to the repository.
            ref: Ref to reset to.
            timeout: Timeout in seconds.

        Raises:
            GitError: If reset fails.
        """
        cmd = ["git", "reset", "--hard", ref]

        logger.debug(f"Running: {' '.join(cmd)} in {repo_path}")

        try:
            subprocess.run(
                cmd,
                cwd=repo_path,
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            raise GitError(f"Reset failed: {stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise GitError(f"Reset timed out after {timeout}s") from e

    def get_current_branch(self, repo_path: Path, timeout: int = 10) -> str | None:
        """Get the current branch name.

        Args:
            repo_path: Path to the repository.
            timeout: Timeout in seconds.

        Returns:
            Branch name, or None if in detached HEAD state.

        Raises:
            GitError: If command fails.
        """
        cmd = ["git", "rev-parse", "--abbrev-ref", "HEAD"]

        logger.debug(f"Running: {' '.join(cmd)} in {repo_path}")

        try:
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            branch = result.stdout.strip()
            # "HEAD" means detached HEAD state
            return None if branch == "HEAD" else branch
        except subprocess.CalledProcessError as e:
            stderr = e.stderr if e.stderr else str(e)
            raise GitError(f"Failed to get current branch: {stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise GitError(f"Get branch timed out after {timeout}s") from e
