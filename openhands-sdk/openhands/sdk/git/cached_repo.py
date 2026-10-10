"""Git operations for cloning and caching remote repositories.

This module provides utilities for cloning git repositories to a local cache
and keeping them updated. Used by both the skills system and plugin fetching.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from filelock import FileLock, Timeout

from openhands.sdk.git.exceptions import GitCommandError
from openhands.sdk.git.utils import run_git_command
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.redact import (
    redact_url_credentials,
    redact_url_credentials_in_text,
)


logger = get_logger(__name__)

# Default timeout for acquiring cache locks (seconds)
# Consistent with other lock timeouts in the SDK (io/local.py, event_store.py)
DEFAULT_LOCK_TIMEOUT = 30

# Matches a full 40-character git commit SHA (lowercase hex)
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HEAD_REF_PREFIX = "refs/heads/"
_TAG_REF_PREFIX = "refs/tags/"


def _checkout_error(ref: str, error: GitCommandError) -> GitCommandError:
    return GitCommandError(
        message=f"git checkout failed: {error}",
        command=["git", "checkout", redact_url_credentials_in_text(ref)],
        exit_code=error.exit_code,
        stderr=error.stderr,
    )


def _match_ref_pattern(pattern: str, ref: str) -> str | None:
    if "*" not in pattern:
        return "" if pattern == ref else None
    if pattern.count("*") != 1:
        return None

    prefix, suffix = pattern.split("*")
    if not ref.startswith(prefix) or not ref.endswith(suffix):
        return None
    end = len(ref) - len(suffix) if suffix else len(ref)
    if end < len(prefix):
        return None
    return ref[len(prefix) : end]


def _refspec_maps_branch(refspec: str, branch: str) -> bool:
    refspec = refspec.removeprefix("+")
    if refspec.startswith("^"):
        return False

    source_pattern, separator, destination_pattern = refspec.partition(":")
    if not separator:
        return False
    source_ref = f"{_HEAD_REF_PREFIX}{branch}"
    wildcard = _match_ref_pattern(source_pattern, source_ref)
    if wildcard is None:
        return False
    destination = destination_pattern.replace("*", wildcard)
    return destination == f"refs/remotes/origin/{branch}"


def _refspec_excludes_branch(refspec: str, branch: str) -> bool:
    refspec = refspec.removeprefix("+")
    if not refspec.startswith("^"):
        return False
    source_ref = f"{_HEAD_REF_PREFIX}{branch}"
    return _match_ref_pattern(refspec.removeprefix("^"), source_ref) is not None


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

    def fetch_requested_ref(
        self,
        repo_path: Path,
        ref: str,
        timeout: int = 60,
        *,
        follow_tags: bool = False,
    ) -> None:
        """Fetch one requested branch, tag, or full commit SHA."""
        if _FULL_SHA_RE.fullmatch(ref):
            refspecs = [f"+{ref}:refs/openhands/commits/{ref}"]
        elif ref.startswith(_HEAD_REF_PREFIX):
            branch = ref.removeprefix(_HEAD_REF_PREFIX)
            run_git_command(
                ["git", "check-ref-format", ref],
                cwd=repo_path,
                timeout=timeout,
                expected_failure=True,
            )
            refspecs = [f"+{ref}:refs/remotes/origin/{branch}"]
        elif ref.startswith(_TAG_REF_PREFIX):
            run_git_command(
                ["git", "check-ref-format", ref],
                cwd=repo_path,
                timeout=timeout,
                expected_failure=True,
            )
            refspecs = [f"+{ref}:{ref}"]
        else:
            branch_ref = f"{_HEAD_REF_PREFIX}{ref}"
            tag_ref = f"{_TAG_REF_PREFIX}{ref}"
            run_git_command(
                ["git", "check-ref-format", branch_ref],
                cwd=repo_path,
                timeout=timeout,
                expected_failure=True,
            )
            refspecs = [
                f"+{branch_ref}:refs/remotes/origin/{ref}",
                f"+{tag_ref}:{tag_ref}",
            ]

        last_error: GitCommandError | None = None
        for refspec in refspecs:
            command = ["git", "fetch"]
            if not follow_tags:
                command.append("--no-tags")
            command.extend(["origin", refspec])
            try:
                run_git_command(
                    command,
                    cwd=repo_path,
                    timeout=timeout,
                    expected_failure=True,
                )
            except GitCommandError as e:
                if e.exit_code == -1:
                    raise
                last_error = e
                continue

            return

        assert last_error is not None
        raise last_error

    def needs_explicit_branch_fetch(
        self,
        repo_path: Path,
        branch: str,
        timeout: int = 10,
    ) -> bool:
        """Return whether a branch is neither mapped nor explicitly excluded."""
        try:
            configured = run_git_command(
                ["git", "config", "--get-all", "remote.origin.fetch"],
                cwd=repo_path,
                timeout=timeout,
                expected_failure=True,
            )
        except GitCommandError as e:
            if e.exit_code == -1:
                raise
            return True

        refspecs = configured.splitlines()
        if any(_refspec_excludes_branch(refspec, branch) for refspec in refspecs):
            return False
        return not any(_refspec_maps_branch(refspec, branch) for refspec in refspecs)

    def checkout(
        self,
        repo_path: Path,
        ref: str,
        timeout: int = 30,
    ) -> None:
        """Checkout a ref (branch, tag, or commit).

        Args:
            repo_path: Path to the repository.
            ref: Branch, tag, or commit to checkout.
            timeout: Timeout in seconds.

        Raises:
            GitCommandError: If checkout fails.
        """
        run_git_command(["git", "checkout", ref], cwd=repo_path, timeout=timeout)

    def checkout_requested_ref(
        self,
        repo_path: Path,
        ref: str,
        timeout: int = 30,
    ) -> None:
        """Checkout a ref only after it resolves to a commit."""
        if ref.startswith(_HEAD_REF_PREFIX):
            branch = ref.removeprefix(_HEAD_REF_PREFIX)
            checkout_args = [branch]
        elif ref.startswith("refs/"):
            branch = None
            checkout_args = [ref]
        else:
            branch = ref
            checkout_args = [ref]
        try:
            resolved_ref = run_git_command(
                [
                    "git",
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    f"{ref}^{{commit}}",
                ],
                cwd=repo_path,
                timeout=timeout,
                expected_failure=True,
            )
        except GitCommandError as e:
            if e.exit_code == -1 or _FULL_SHA_RE.fullmatch(ref) or branch is None:
                raise _checkout_error(ref, e) from e
            assert branch is not None
            remote_ref = f"refs/remotes/origin/{branch}"
            try:
                resolved_remote_ref = run_git_command(
                    [
                        "git",
                        "rev-parse",
                        "--verify",
                        "--end-of-options",
                        f"{remote_ref}^{{commit}}",
                    ],
                    cwd=repo_path,
                    timeout=timeout,
                    expected_failure=True,
                )
            except GitCommandError as remote_error:
                raise _checkout_error(ref, remote_error) from remote_error
            checkout_args = (
                ["--detach", resolved_remote_ref]
                if branch.startswith("-")
                else ["-b", branch, resolved_remote_ref]
            )
        else:
            if branch is not None and branch.startswith("-"):
                checkout_args = ["--detach", resolved_ref]

        run_git_command(
            ["git", "checkout", *checkout_args], cwd=repo_path, timeout=timeout
        )

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
    *,
    require_requested_ref: bool = False,
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
        require_requested_ref: Return None instead of using another cached checkout
            when the requested ref cannot be checked out.

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
            return _do_clone_or_update(
                url,
                repo_path,
                ref,
                update,
                git,
                require_requested_ref=require_requested_ref,
            )
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
    *,
    require_requested_ref: bool = False,
) -> Path:
    """Perform the actual clone or update operation (called while holding lock).

    Args:
        url: Git URL to clone.
        repo_path: Path where the repository should be cached.
        ref: Branch, tag, or commit to checkout.
        update: Whether to update existing repos.
        git: GitHelper instance.
        require_requested_ref: Whether failure to check out a requested ref should
            fail the operation instead of falling back to another cached checkout.

    Returns:
        Path to the repository.

    Raises:
        GitCommandError: If git operations fail.
    """
    if repo_path.exists() and (repo_path / ".git").exists():
        if update:
            logger.debug(f"Updating repository at {repo_path}")
            _update_repository(
                repo_path,
                ref,
                git,
                require_requested_ref=require_requested_ref,
            )
        elif ref:
            logger.debug(f"Checking out ref {ref} at {repo_path}")
            if require_requested_ref:
                _checkout_requested_ref(repo_path, ref, git)
            else:
                _checkout_ref(repo_path, ref, git)
        else:
            logger.debug(f"Using cached repository at {repo_path}")
    else:
        logger.info(f"Cloning repository from {redact_url_credentials(url)}")
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
        branch: Branch or tag to checkout during clone. For full 40-character
            commit SHAs, ``--branch`` is not used; the default branch is cloned
            in full (no ``--depth``) and the SHA is checked out afterward.
        git: GitHelper instance.
    """
    # Remove existing directory if it exists but isn't a valid git repo
    if dest.exists():
        shutil.rmtree(dest)

    if branch and _FULL_SHA_RE.fullmatch(branch):
        # git clone --branch does not accept raw commit SHAs. Clone the full
        # history without specifying a branch, then checkout the target commit.
        git.clone(url, dest, depth=None, branch=None)
        git.checkout(dest, branch)
    else:
        git.clone(url, dest, depth=1, branch=branch)
    logger.debug(f"Repository cloned to {dest}")


def _update_repository(
    repo_path: Path,
    ref: str | None,
    git: GitHelper,
    *,
    require_requested_ref: bool = False,
) -> None:
    """Update an existing cached repository to the latest remote state.

    For a specific ref, first attempts a purely local checkout. If that checkout
    lands in detached HEAD (the ref is a tag or commit SHA already present in the
    local object store), the fetch is skipped entirely — immutable refs never
    change, so the cached objects are already correct. This lets air-gapped clients
    with a pre-populated cache work without any network access.

    If the local checkout fails (ref not yet cached) or lands on a branch (needs
    the remote's latest), falls through to the requested fetch → checkout → reset
    cycle. On any failure, logs a warning and returns silently so the cached
    repository remains usable.

    Behavior by scenario:
        1. ref locally present and immutable (detached HEAD): local checkout only.
        2. ref specified but requires fetch: fetch -> checkout + reset.
        3. ref is None, on a branch: fetch configured refs, supplement the current
           branch if needed, then reset to its origin ref.
        4. ref is None, detached HEAD: fetch -> checkout default branch -> reset.

    Args:
        repo_path: Path to the repository.
        ref: Branch, tag, or commit to update to. If None, uses current branch
            or falls back to the remote's default branch.
        git: GitHelper instance.
        require_requested_ref: Raise when a requested ref cannot be checked out
            instead of leaving the repository on another cached checkout.
    """
    ref_missing_locally = False
    if ref:
        # Optimistically attempt a local checkout before touching the network.
        # Detached HEAD after checkout means the ref is a tag or commit SHA that
        # is already present in the local object store — skip the fetch entirely.
        try:
            if require_requested_ref:
                git.checkout_requested_ref(repo_path, ref)
            else:
                git.checkout(repo_path, ref)
            if git.get_current_branch(repo_path) is None:
                logger.debug("Ref %r already present locally; skipping fetch", ref)
                return
        except GitCommandError:
            ref_missing_locally = True

    if require_requested_ref:
        assert ref is not None
        if ref_missing_locally:
            git.fetch_requested_ref(repo_path, ref)
        elif not _try_fetch_requested_ref(repo_path, ref, git):
            return
    elif ref and not _try_fetch(repo_path, git):
        return

    # If a specific ref was requested, check it out
    if ref:
        if require_requested_ref:
            _checkout_requested_ref(repo_path, ref, git)
        else:
            _try_checkout_and_reset(repo_path, ref, git)
        return

    # No ref specified - update based on current state
    if not _try_fetch(repo_path, git):
        return
    current_branch = git.get_current_branch(repo_path)

    if current_branch:
        branch_ref = f"{_HEAD_REF_PREFIX}{current_branch}"
        if git.needs_explicit_branch_fetch(repo_path, current_branch):
            if not _try_fetch_requested_ref(
                repo_path,
                branch_ref,
                git,
                follow_tags=True,
            ):
                return
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


def _try_fetch_requested_ref(
    repo_path: Path,
    ref: str,
    git: GitHelper,
    *,
    follow_tags: bool = False,
) -> bool:
    """Fetch one requested ref, retaining its cached checkout on failure."""
    try:
        git.fetch_requested_ref(repo_path, ref, follow_tags=follow_tags)
        return True
    except GitCommandError as e:
        safe_ref = redact_url_credentials_in_text(ref)
        logger.warning(
            f"Failed to fetch requested ref {safe_ref}: {e}. Using cached version."
        )
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

    _reset_checked_out_branch(repo_path, ref, git)


def _checkout_requested_ref(repo_path: Path, ref: str, git: GitHelper) -> None:
    """Checkout a verified requested ref and update its local branch."""
    logger.debug(f"Checking out requested ref: {ref}")
    git.checkout_requested_ref(repo_path, ref)

    _reset_checked_out_branch(repo_path, ref, git)


def _reset_checked_out_branch(repo_path: Path, ref: str, git: GitHelper) -> None:
    """Reset a checked-out branch to its corresponding remote ref."""

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
