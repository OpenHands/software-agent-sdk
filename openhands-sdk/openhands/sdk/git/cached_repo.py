"""Git operations for cloning and caching remote repositories.

This module provides utilities for cloning git repositories to a local cache
and keeping them updated. Used by both the skills system and plugin fetching.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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

# Matches full and abbreviated Git object IDs.
_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_ABBREVIATED_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,39}$")
_HEAD_REF_PREFIX: Final[str] = "refs/heads/"
_TAG_REF_PREFIX: Final[str] = "refs/tags/"
_REMOTE_REF_PREFIX: Final[str] = "refs/remotes/origin/"
_REQUESTED_BRANCH_CONFIG: Final[str] = "openhands.requested-branch"
_SYMBOLIC_DEFAULT_REFS: Final[frozenset[str]] = frozenset(
    {
        "HEAD",
        "refs/heads/HEAD",
        "origin/HEAD",
        "refs/remotes/origin/HEAD",
    }
)


@dataclass(frozen=True)
class _RequestedRef:
    source: str
    cached: str | None
    pinned: bool = False


def _requested_ref_candidates(ref: str) -> tuple[list[_RequestedRef], bool]:
    """Return remote candidates and whether ``ref`` can be an object ID."""
    if _FULL_SHA_RE.fullmatch(ref):
        return [], True
    if ref.startswith(_HEAD_REF_PREFIX):
        branch = ref.removeprefix(_HEAD_REF_PREFIX)
        return [_RequestedRef(ref, f"{_REMOTE_REF_PREFIX}{branch}")], False
    if ref.startswith(_TAG_REF_PREFIX):
        return [_RequestedRef(ref, ref, pinned=True)], False
    if ref.startswith(_REMOTE_REF_PREFIX):
        branch = ref.removeprefix(_REMOTE_REF_PREFIX)
        return [_RequestedRef(f"{_HEAD_REF_PREFIX}{branch}", ref)], False
    if ref.startswith("origin/"):
        branch = ref.removeprefix("origin/")
        return [
            _RequestedRef(
                f"{_HEAD_REF_PREFIX}{branch}",
                f"{_REMOTE_REF_PREFIX}{branch}",
            )
        ], False
    if ref.startswith("refs/"):
        return [_RequestedRef(ref, None)], False

    return [
        _RequestedRef(
            f"{_HEAD_REF_PREFIX}{ref}",
            f"{_REMOTE_REF_PREFIX}{ref}",
        ),
        _RequestedRef(
            f"{_TAG_REF_PREFIX}{ref}",
            f"{_TAG_REF_PREFIX}{ref}",
            pinned=True,
        ),
    ], bool(_ABBREVIATED_SHA_RE.fullmatch(ref))


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
                shallow clones initially fetch only the selected branch tip.
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
        allow_cached: bool = False,
    ) -> str:
        """Fetch ``ref`` and return its full commit ID.

        ``allow_cached`` permits fallback only when the remote cannot refresh a
        branch, tag, or object ID that has a matching local identity.
        """
        candidates, can_be_object_id = self.validate_requested_ref(
            repo_path, ref, timeout
        )
        if candidates:
            try:
                selected = self._select_remote_ref(repo_path, candidates, timeout)
            except GitCommandError as error:
                if error.exit_code != 2:
                    if allow_cached and (
                        cached_commit := self.resolve_local_requested_ref(
                            repo_path, ref, timeout
                        )
                    ):
                        self._log_cached_ref_fallback(ref, error)
                        return cached_commit
                    raise
                if not can_be_object_id:
                    raise
            else:
                try:
                    self._fetch_selected_ref(
                        repo_path,
                        selected,
                        timeout,
                        follow_tags=follow_tags,
                    )
                except GitCommandError as fetch_error:
                    try:
                        self._select_remote_ref(repo_path, [selected], timeout)
                    except GitCommandError as probe_error:
                        if probe_error.exit_code == 2:
                            raise probe_error from fetch_error
                    if (
                        allow_cached
                        and selected.cached is not None
                        and (
                            cached_commit := self._try_resolve_cached_ref(
                                repo_path, selected.cached, timeout
                            )
                        )
                    ):
                        self._log_cached_ref_fallback(ref, fetch_error)
                        return cached_commit
                    raise fetch_error

                try:
                    commit = self._resolve_commit(repo_path, "FETCH_HEAD", timeout)
                    object_id = self._resolve_object(repo_path, "FETCH_HEAD", timeout)
                except GitCommandError as error:
                    if selected.cached is not None:
                        self._delete_cached_ref(repo_path, selected.cached, timeout)
                    raise GitCommandError(
                        "Remote ref did not resolve to a commit",
                        command=[
                            "git",
                            "fetch",
                            "origin",
                            redact_url_credentials_in_text(selected.source),
                        ],
                        exit_code=2,
                    ) from error

                self._cache_selected_ref(
                    repo_path,
                    selected,
                    object_id,
                    timeout,
                )
                return commit

        if can_be_object_id:
            try:
                return self._fetch_object_id(repo_path, ref, timeout)
            except GitCommandError as error:
                if allow_cached and (
                    cached_commit := self._try_resolve_object_id(
                        repo_path, ref, timeout
                    )
                ):
                    self._log_cached_ref_fallback(ref, error)
                    return cached_commit
                raise

        raise GitCommandError(
            "Git remote did not return the requested ref",
            command=["git", "ls-remote", "origin", redact_url_credentials_in_text(ref)],
            exit_code=2,
        )

    def validate_requested_ref(
        self,
        repo_path: Path,
        ref: str,
        timeout: int = 10,
    ) -> tuple[list[_RequestedRef], bool]:
        """Validate ``ref`` and return its normalized candidates."""
        if ref in _SYMBOLIC_DEFAULT_REFS:
            raise GitCommandError(
                "Symbolic default refs are not supported",
                command=["git", "check-ref-format", ref],
                exit_code=1,
            )

        candidates, can_be_object_id = _requested_ref_candidates(ref)
        if _FULL_SHA_RE.fullmatch(ref):
            return candidates, can_be_object_id

        if ref.startswith("refs/"):
            name = candidates[0].source
            command = ["git", "check-ref-format", name]
        elif ref.startswith("origin/"):
            name = ref.removeprefix("origin/")
            command = ["git", "check-ref-format", "--branch", name]
        else:
            command = ["git", "check-ref-format", "--branch", ref]
        run_git_command(
            command,
            cwd=repo_path,
            timeout=timeout,
            expected_failure=True,
        )
        return candidates, can_be_object_id

    def resolve_local_requested_ref(
        self,
        repo_path: Path,
        ref: str,
        timeout: int = 10,
    ) -> str | None:
        """Resolve an exact cached ref without changing the checkout."""
        candidates, can_be_object_id = self.validate_requested_ref(
            repo_path, ref, timeout
        )
        for candidate in candidates:
            if candidate.cached is None:
                continue
            if resolved := self._try_resolve_cached_ref(
                repo_path, candidate.cached, timeout
            ):
                return resolved
        if can_be_object_id:
            return self._try_resolve_object_id(repo_path, ref, timeout)
        return None

    def requested_ref_is_pinned(
        self,
        repo_path: Path,
        ref: str,
        timeout: int = 10,
    ) -> bool:
        """Return whether the cached requested ref is a tag or object ID."""
        candidates, can_be_object_id = self.validate_requested_ref(
            repo_path, ref, timeout
        )
        for candidate in candidates:
            if candidate.cached is None:
                continue
            if self._try_resolve_cached_ref(repo_path, candidate.cached, timeout):
                return candidate.pinned
        return can_be_object_id and bool(
            self._try_resolve_object_id(repo_path, ref, timeout)
        )

    def checkout_detached(
        self,
        repo_path: Path,
        commit: str,
        timeout: int = 30,
    ) -> None:
        """Checkout one verified commit without creating a local branch."""
        run_git_command(
            ["git", "checkout", "--force", "--detach", commit],
            cwd=repo_path,
            timeout=timeout,
        )

    def remember_requested_ref(
        self,
        repo_path: Path,
        ref: str,
        commit: str,
        timeout: int = 10,
    ) -> None:
        """Store a selected branch, or clear the marker for other refs."""
        candidates, _ = self.validate_requested_ref(repo_path, ref, timeout)
        for candidate in candidates:
            if (
                candidate.source.startswith(_HEAD_REF_PREFIX)
                and candidate.cached is not None
                and self._try_resolve_cached_ref(repo_path, candidate.cached, timeout)
                == commit
            ):
                run_git_command(
                    [
                        "git",
                        "config",
                        "--local",
                        _REQUESTED_BRANCH_CONFIG,
                        candidate.source,
                    ],
                    cwd=repo_path,
                    timeout=timeout,
                )
                return
        self.clear_requested_branch(repo_path, timeout)

    def get_requested_branch(
        self,
        repo_path: Path,
        timeout: int = 10,
    ) -> str | None:
        """Return the branch selected by the last explicit ref request."""
        try:
            return run_git_command(
                ["git", "config", "--get", _REQUESTED_BRANCH_CONFIG],
                cwd=repo_path,
                timeout=timeout,
                expected_failure=True,
            )
        except GitCommandError as error:
            if error.exit_code != 1:
                raise
            return None

    def clear_requested_branch(
        self,
        repo_path: Path,
        timeout: int = 10,
    ) -> None:
        """Clear the branch used for no-ref updates."""
        if self.get_requested_branch(repo_path, timeout) is None:
            return
        run_git_command(
            ["git", "config", "--local", "--unset-all", _REQUESTED_BRANCH_CONFIG],
            cwd=repo_path,
            timeout=timeout,
        )

    def _select_remote_ref(
        self,
        repo_path: Path,
        candidates: list[_RequestedRef],
        timeout: int,
    ) -> _RequestedRef:
        command = [
            "git",
            "ls-remote",
            "--exit-code",
            "--refs",
            "origin",
            *(candidate.source for candidate in candidates),
        ]
        try:
            output = run_git_command(
                command,
                cwd=repo_path,
                timeout=timeout,
                expected_failure=True,
            )
        except GitCommandError as error:
            if error.exit_code == 2:
                for candidate in candidates:
                    if candidate.cached is not None:
                        self._delete_cached_ref(repo_path, candidate.cached, timeout)
            raise

        available = {
            line.split("\t", 1)[1] for line in output.splitlines() if "\t" in line
        }
        for candidate in candidates:
            if candidate.cached is not None and candidate.source not in available:
                self._delete_cached_ref(repo_path, candidate.cached, timeout)
        for candidate in candidates:
            if candidate.source in available:
                return candidate

        safe_command = [redact_url_credentials_in_text(part) for part in command]
        raise GitCommandError(
            "Git remote did not return the requested ref",
            command=safe_command,
            exit_code=2,
        )

    def _fetch_selected_ref(
        self,
        repo_path: Path,
        selected: _RequestedRef,
        timeout: int,
        *,
        follow_tags: bool,
    ) -> None:
        command = ["git", "fetch"]
        if not follow_tags:
            command.append("--no-tags")
        if self._is_shallow(repo_path, timeout):
            command.extend(["--depth", "1"])
        if follow_tags and selected.cached is not None:
            if self._is_symbolic_ref(repo_path, selected.cached, timeout):
                self._delete_cached_ref(repo_path, selected.cached, timeout)
            refspec = f"+{selected.source}:{selected.cached}"
        else:
            refspec = selected.source
        command.extend(["origin", refspec])
        run_git_command(
            command,
            cwd=repo_path,
            timeout=timeout,
            expected_failure=True,
        )

    def _cache_selected_ref(
        self,
        repo_path: Path,
        selected: _RequestedRef,
        object_id: str,
        timeout: int,
    ) -> None:
        """Cache a validated branch or tag."""
        if selected.cached is not None:
            run_git_command(
                ["git", "update-ref", "--no-deref", selected.cached, object_id],
                cwd=repo_path,
                timeout=timeout,
            )

    def _log_cached_ref_fallback(
        self,
        ref: str,
        error: GitCommandError,
    ) -> None:
        safe_ref = redact_url_credentials_in_text(ref)
        logger.warning(
            f"Could not refresh requested ref {safe_ref}: {error}. "
            "Using the matching cached ref."
        )

    def _fetch_object_id(
        self,
        repo_path: Path,
        ref: str,
        timeout: int,
    ) -> str:
        command = ["git", "fetch", "--no-tags"]
        if self._is_shallow(repo_path, timeout):
            command.extend(["--depth", "1"])
        command.extend(["origin", ref])
        try:
            run_git_command(
                command,
                cwd=repo_path,
                timeout=timeout,
                expected_failure=True,
            )
        except GitCommandError as fetch_error:
            if fetch_error.exit_code == -1:
                raise
            self.fetch(repo_path, timeout=timeout)
            resolved = self._try_resolve_object_id(repo_path, ref, timeout)
            if resolved is None:
                self._fetch_all_remote_history(repo_path, timeout)
                resolved = self._try_resolve_object_id(repo_path, ref, timeout)
            if resolved is None:
                raise GitCommandError(
                    "Fetched object does not match the requested ID",
                    command=["git", "fetch", "origin", ref],
                    exit_code=1,
                ) from fetch_error
            return resolved

        resolved = self._resolve_commit(repo_path, "FETCH_HEAD", timeout)
        if not resolved.casefold().startswith(ref.casefold()):
            raise GitCommandError(
                "Fetched object does not match the requested ID",
                command=["git", "fetch", "origin", ref],
                exit_code=1,
            )
        return resolved

    def _fetch_all_remote_history(self, repo_path: Path, timeout: int) -> None:
        """Fetch all remote branch and tag history."""
        command = ["git", "fetch"]
        if self._is_shallow(repo_path, timeout):
            command.append("--unshallow")
        command.extend(
            [
                "origin",
                f"+{_HEAD_REF_PREFIX}*:{_REMOTE_REF_PREFIX}*",
                f"+{_TAG_REF_PREFIX}*:{_TAG_REF_PREFIX}*",
            ]
        )
        run_git_command(
            command,
            cwd=repo_path,
            timeout=timeout,
            expected_failure=True,
        )

    def _resolve_commit(
        self,
        repo_path: Path,
        ref: str,
        timeout: int,
    ) -> str:
        return run_git_command(
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

    def _resolve_object(
        self,
        repo_path: Path,
        ref: str,
        timeout: int,
    ) -> str:
        return run_git_command(
            ["git", "rev-parse", "--verify", "--end-of-options", ref],
            cwd=repo_path,
            timeout=timeout,
            expected_failure=True,
        )

    def _try_resolve_commit(
        self,
        repo_path: Path,
        ref: str,
        timeout: int,
    ) -> str | None:
        try:
            return self._resolve_commit(repo_path, ref, timeout)
        except GitCommandError as error:
            if error.exit_code == -1:
                raise
            return None

    def _try_resolve_cached_ref(
        self,
        repo_path: Path,
        ref: str,
        timeout: int,
    ) -> str | None:
        """Resolve a cached identity only when it is a direct ref."""
        if self._is_symbolic_ref(repo_path, ref, timeout):
            return None
        return self._try_resolve_commit(repo_path, ref, timeout)

    def _is_symbolic_ref(
        self,
        repo_path: Path,
        ref: str,
        timeout: int,
    ) -> bool:
        try:
            run_git_command(
                ["git", "symbolic-ref", "-q", ref],
                cwd=repo_path,
                timeout=timeout,
                expected_failure=True,
            )
        except GitCommandError as error:
            if error.exit_code != 1:
                raise
            return False
        return True

    def _try_resolve_object_id(
        self,
        repo_path: Path,
        ref: str,
        timeout: int,
    ) -> str | None:
        """Resolve ``ref`` only when the commit matches its object ID prefix."""
        commit = self._try_resolve_commit(repo_path, ref, timeout)
        if commit is None or not commit.casefold().startswith(ref.casefold()):
            return None
        return commit

    def _is_shallow(self, repo_path: Path, timeout: int) -> bool:
        return (
            run_git_command(
                ["git", "rev-parse", "--is-shallow-repository"],
                cwd=repo_path,
                timeout=timeout,
            )
            == "true"
        )

    def _delete_cached_ref(
        self,
        repo_path: Path,
        ref: str,
        timeout: int,
    ) -> None:
        run_git_command(
            ["git", "update-ref", "--no-deref", "-d", ref],
            cwd=repo_path,
            timeout=timeout,
        )

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
    strict: bool = False,
) -> Path | None:
    """Clone or update a git repository in a cache directory.

    This is the main entry point for cached repository operations.

    Behavior:
        - If repo doesn't exist: clone shallow, then resolve an optional ref
        - If repo exists and update=True: fetch and resolve an optional ref
        - If repo exists and update=False with ref: resolve a cached branch,
          tag, or object ID
        - If repo exists and update=False without ref: use as-is

    A requested ref must resolve to Git content. A confirmed missing remote ref
    fails. When ``strict`` is false, an unavailable remote can use an exact
    matching cached branch, tag, or object ID.

    Concurrency:
        Uses file-based locking to prevent race conditions when multiple processes
        access the same cache directory. The lock file is created adjacent to the
        repo directory (repo_path.lock).

    Args:
        url: Git URL to clone.
        repo_path: Path where the repository should be cached.
        ref: Branch, tag, or commit to checkout. If None, update the selected
            branch or recover to the default branch.
        update: If True and repo exists, fetch and update it. If False, skip fetch.
        git_helper: GitHelper instance for git operations. If None, creates one.
        lock_timeout: Timeout in seconds for acquiring the lock. Default is 30
            seconds.
        strict: If True, a failed requested-ref refresh or no-ref fetch returns
            None instead of using a cached ref. A full object ID that is already
            resolved needs no refresh. A confirmed missing requested ref always
            fails. Best-effort reset and default-branch recovery stay lenient.

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
                strict=strict,
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
    strict: bool = False,
) -> Path:
    """Perform the actual clone or update operation (called while holding lock).

    Args:
        url: Git URL to clone.
        repo_path: Path where the repository should be cached.
        ref: Branch, tag, or commit to checkout.
        update: Whether to update existing repos.
        git: GitHelper instance.
        strict: Whether a failed requested-ref refresh or no-ref fetch raises.

    Returns:
        Path to the repository.

    Raises:
        GitCommandError: If git operations fail.
    """
    if repo_path.exists() and (repo_path / ".git").exists():
        if ref:
            _resolve_and_checkout_requested_ref(
                repo_path,
                ref,
                git,
                update=update,
                strict=strict,
            )
        elif update:
            logger.debug(f"Updating repository at {repo_path}")
            _update_repository(repo_path, git, strict=strict)
        else:
            logger.debug(f"Using cached repository at {repo_path}")
    else:
        logger.info(f"Cloning repository from {redact_url_credentials(url)}")
        _clone_repository(url, repo_path, git)
        if ref:
            _resolve_and_checkout_requested_ref(
                repo_path,
                ref,
                git,
                update=True,
                strict=strict,
            )

    return repo_path


def _clone_repository(
    url: str,
    dest: Path,
    git: GitHelper,
) -> None:
    """Clone a git repository.

    Args:
        url: Git URL to clone.
        dest: Destination path.
        git: GitHelper instance.
    """
    # Remove existing directory if it exists but isn't a valid git repo
    if dest.exists():
        shutil.rmtree(dest)

    git.clone(url, dest, depth=1, branch=None)
    logger.debug(f"Repository cloned to {dest}")


def _update_repository(
    repo_path: Path,
    git: GitHelper,
    strict: bool = False,
) -> None:
    """Update a cached repository without a new requested ref.

    Behavior by scenario:
        1. A branch selected by an earlier explicit request remains selected.
        2. On a local branch: fetch configured refs, supplement the current
           branch if needed, then reset to its origin ref.
        3. On other detached HEAD states: fetch, then checkout and reset the
           default branch.

    Args:
        repo_path: Path to the repository.
        git: GitHelper instance.
        strict: If True, a failed fetch raises ``GitCommandError`` instead of
            keeping the cache.
    """
    requested_branch = git.get_requested_branch(repo_path)
    if requested_branch is not None:
        try:
            _resolve_and_checkout_requested_ref(
                repo_path,
                requested_branch,
                git,
                update=True,
                strict=strict,
                follow_tags=True,
            )
        except GitCommandError as error:
            if strict:
                raise
            if error.exit_code != 2:
                logger.warning(
                    f"Failed to update requested branch: {error}. "
                    "Using the cached version."
                )
                return
            git.clear_requested_branch(repo_path)
        else:
            return

    if strict:
        git.fetch(repo_path)
    elif not _try_fetch(repo_path, git):
        return
    current_branch = git.get_current_branch(repo_path)

    if current_branch:
        branch_ref = f"{_HEAD_REF_PREFIX}{current_branch}"
        if git.needs_explicit_branch_fetch(repo_path, current_branch):
            if strict:
                git.fetch_requested_ref(repo_path, branch_ref, follow_tags=True)
            elif not _try_fetch_requested_ref(
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
    """Fetch one requested ref and return whether the fetch succeeded."""
    try:
        git.fetch_requested_ref(repo_path, ref, follow_tags=follow_tags)
        return True
    except GitCommandError as e:
        safe_ref = redact_url_credentials_in_text(ref)
        logger.warning(
            f"Failed to fetch requested ref {safe_ref}: {e}. Using cached version."
        )
        return False


def _resolve_and_checkout_requested_ref(
    repo_path: Path,
    ref: str,
    git: GitHelper,
    *,
    update: bool,
    strict: bool,
    follow_tags: bool = False,
) -> None:
    """Resolve one requested ref, then check out its exact commit."""
    safe_ref = redact_url_credentials_in_text(ref)
    if not update:
        cached_commit = git.resolve_local_requested_ref(repo_path, ref)
        if cached_commit is None:
            raise GitCommandError(
                "Requested ref is not available in the cache",
                command=["git", "rev-parse", safe_ref],
                exit_code=1,
            )
        git.checkout_detached(repo_path, cached_commit)
        git.remember_requested_ref(repo_path, ref, cached_commit)
        return

    if _FULL_SHA_RE.fullmatch(ref):
        cached_commit = git.resolve_local_requested_ref(repo_path, ref)
        if cached_commit is not None:
            git.checkout_detached(repo_path, cached_commit)
            git.remember_requested_ref(repo_path, ref, cached_commit)
            return

    commit = git.fetch_requested_ref(
        repo_path,
        ref,
        follow_tags=follow_tags,
        allow_cached=not strict,
    )

    git.checkout_detached(repo_path, commit)
    git.remember_requested_ref(repo_path, ref, commit)


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
    """Recover from detached HEAD state at the default branch commit.

    This handles the scenario where:
    1. User previously fetched an explicit non-branch ref -> detached HEAD
    2. User now fetches with update=True but no ref -> expects "latest"

    Without this recovery, the repo would stay stuck on the old ref. Resolving
    the remote-tracking ref avoids passing a remote-controlled name to checkout.
    """
    default_branch = git.get_default_branch(repo_path)

    if not default_branch:
        logger.warning(
            "Repository is in detached HEAD state and default branch could not be "
            "determined. Specify a ref explicitly to update, or the cached version "
            "will be used as-is."
        )
        return

    default_ref = f"{_REMOTE_REF_PREFIX}{default_branch}"
    commit = git.resolve_local_requested_ref(repo_path, default_ref)
    if commit is None:
        logger.warning(
            "Repository is in detached HEAD state and the default branch commit "
            "could not be resolved. The cached version will be used as-is."
        )
        return

    try:
        git.checkout_detached(repo_path, commit)
        logger.debug("Repository updated to the default branch commit")
    except GitCommandError as e:
        logger.warning(
            f"Failed to checkout the default branch commit: {e}. Using cached version."
        )
