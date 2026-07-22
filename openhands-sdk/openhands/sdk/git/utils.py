import logging
import re
import shlex
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from openhands.sdk.git.exceptions import GitCommandError, GitRepositoryError
from openhands.sdk.utils.redact import (
    redact_url_credentials,
    redact_url_credentials_in_text,
    redact_url_params,
)


logger = logging.getLogger(__name__)

# Git empty tree hash - this is a well-known constant in git
# representing the hash of an empty tree object
GIT_EMPTY_TREE_HASH = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _run_git_subprocess(
    args: list[str],
    cwd: str | Path | None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with the capture/decode settings all git callers need."""
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        timeout=timeout,
    )


def _run_git_probe(args: list[str], cwd: str | Path, timeout: int = 30) -> str:
    try:
        result = _run_git_subprocess(["git", "--no-pager", *args], cwd, timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def get_git_repository_metadata(
    repo_dir: str | Path, timeout: int = 30
) -> dict[str, str]:
    """Return best-effort repository identity metadata."""
    metadata: dict[str, str] = {}
    remote = _run_git_probe(["remote", "get-url", "origin"], repo_dir, timeout)
    if remote:
        metadata["repo_remote"] = redact_url_params(
            redact_url_credentials_in_text(remote)
        )

    head_and_branch = _run_git_probe(
        ["rev-parse", "HEAD", "--abbrev-ref", "HEAD"], repo_dir, timeout
    )
    lines = head_and_branch.splitlines()
    if len(lines) == 2:
        head, branch = lines
        metadata["head_commit"] = head
        metadata["branch"] = "DETACHED" if branch == "HEAD" else branch
    return metadata


def run_git_command(
    args: list[str],
    cwd: str | Path | None = None,
    timeout: int = 30,
) -> str:
    """Run a git command safely without shell injection vulnerabilities.

    Args:
        args: List of command arguments (e.g., ['git', 'status', '--porcelain'])
        cwd: Working directory to run the command in (optional for commands like clone)
        timeout: Timeout in seconds (default: 30)

    Returns:
        Command output as string

    Raises:
        GitCommandError: If the git command fails
    """
    redacted_args = [redact_url_credentials(a) for a in args]
    cmd_str = shlex.join(redacted_args)

    try:
        result = _run_git_subprocess(args, cwd, timeout)

        if result.returncode != 0:
            error_msg = f"Git command failed: {cmd_str}"
            # stderr can echo the remote URL (with embedded credentials on some
            # git versions / error paths), so redact before logging and storing.
            redacted_stderr = redact_url_credentials_in_text(result.stderr)
            logger.error(
                f"{error_msg}. Exit code: {result.returncode}. "
                f"Stderr: {redacted_stderr}"
            )
            raise GitCommandError(
                message=error_msg,
                command=redacted_args,
                exit_code=result.returncode,
                stderr=redacted_stderr.strip(),
            )

        logger.debug(f"Git command succeeded: {cmd_str}")
        return result.stdout.strip()

    except subprocess.TimeoutExpired as e:
        error_msg = f"Git command timed out: {cmd_str}"
        logger.error(error_msg)
        raise GitCommandError(
            message=error_msg,
            command=redacted_args,
            exit_code=-1,
            stderr="Command timed out",
        ) from e
    except FileNotFoundError as e:
        error_msg = "Git command not found. Is git installed?"
        logger.error(error_msg)
        raise GitCommandError(
            message=error_msg,
            command=redacted_args,
            exit_code=-1,
            stderr="Git executable not found",
        ) from e


def _repo_has_commits(repo_dir: str | Path) -> bool:
    """Check if a git repository has any commits.

    Uses 'git rev-list --count --all' which returns "0" for empty repos
    without failing, avoiding ERROR logs for expected conditions.

    Args:
        repo_dir: Path to the git repository

    Returns:
        True if the repository has at least one commit, False otherwise
    """
    try:
        count = run_git_command(
            ["git", "--no-pager", "rev-list", "--count", "--all"], repo_dir
        )
        return count.strip() != "0"
    except GitCommandError:
        logger.debug("Could not check commit count")
        return False


def _rev_parse(repo_dir: str | Path, ref: str) -> str | None:
    """Resolve ``ref`` to a commit SHA, or None if it doesn't resolve."""
    try:
        result = run_git_command(
            ["git", "--no-pager", "rev-parse", "--verify", ref], repo_dir
        )
        return result or None
    except GitCommandError:
        return None


def _merge_base(repo_dir: str | Path, ref_a: str, ref_b: str) -> str | None:
    """Return the merge base of two refs, or None if it can't be computed
    (e.g. unrelated histories, shallow clone)."""
    try:
        result = run_git_command(
            ["git", "--no-pager", "merge-base", ref_a, ref_b], repo_dir
        )
        return result or None
    except GitCommandError:
        return None


def _get_current_branch(repo_dir: str | Path) -> str | None:
    """Return the current branch name, or None when detached/unborn."""
    try:
        branch = run_git_command(
            ["git", "--no-pager", "rev-parse", "--abbrev-ref", "HEAD"], repo_dir
        )
        if branch and branch != "HEAD":
            return branch
    except GitCommandError:
        logger.debug("Could not get current branch name")
    return None


def _get_remote_default_branch(repo_dir: str | Path) -> str | None:
    """Find origin's default branch name (e.g. ``main``).

    Prefers the local ``origin/HEAD`` symref (recorded on clone — no
    network); falls back to ``git remote show origin`` (a network call)
    for remotes that were added without recording ``origin/HEAD``.
    """
    try:
        symref = run_git_command(
            ["git", "--no-pager", "rev-parse", "--abbrev-ref", "origin/HEAD"],
            repo_dir,
        )
        prefix = "origin/"
        if symref.startswith(prefix) and len(symref) > len(prefix):
            return symref[len(prefix) :]
    except GitCommandError:
        logger.debug("origin/HEAD not set; falling back to `git remote show`")

    try:
        remote_info = run_git_command(
            ["git", "--no-pager", "remote", "show", "origin"], repo_dir
        )
        for line in remote_info.splitlines():
            if "HEAD branch:" in line:
                default_branch = line.split(":")[-1].strip()
                if default_branch and default_branch != "(unknown)":
                    return default_branch
                break
    except GitCommandError:
        logger.debug("Could not get remote information")
    return None


def _has_tracked_changes(repo_dir: str | Path) -> bool:
    """True when the working tree or index differs from HEAD.

    Untracked files are deliberately excluded: they never appear in
    ``git diff <ref>`` output (they're reported separately via
    ``ls-files --others``), so they must not influence base selection —
    a stray scratch file must not re-hide a fully-pushed branch's diff.
    """
    try:
        status = run_git_command(
            ["git", "--no-pager", "status", "--porcelain", "--untracked-files=no"],
            repo_dir,
        )
        return bool(status.strip())
    except GitCommandError:
        # Fail-safe: treat as dirty so the branch keeps comparing against
        # its own upstream instead of surfacing unrelated history.
        return True


def _get_display_base_ref(repo_dir: str | Path) -> str:
    """Auto-detect the base ref for *displaying* what a conversation changed.

    Unlike the export algorithm (which must only pick bases that are
    reconstructable from a fresh clone, because workspace-export patches
    are replayed elsewhere), this picks the base that best answers "what
    has been changed here" — including work that is already committed and
    even pushed. Candidates, in order:

    1. ``origin/<current-branch>`` — but *skipped* when it points at HEAD
       while the tracked working tree is clean: comparing a fully-pushed
       branch against its own upstream renders an empty diff and would
       hide the branch's (PR's) work. A dirty tree keeps this candidate,
       so a pre-existing attached branch shows only the new edits rather
       than the whole branch history.
    2. ``merge-base(HEAD, origin/<default>)`` — the fork point. Preferred
       over the default tip so commits that landed on the default branch
       after the fork don't render as reverse-noise.
    3. ``origin/<default>`` — when the merge base is unavailable (e.g.
       shallow clone).
    4. Without a usable origin: ``merge-base(HEAD, local main|master)``,
       only when that branch is not the current one and is an ancestor of
       HEAD — exactly the no-remote worktree shape (``openhands/<uuid>``
       forked off local ``main``), keeping committed work visible.
    5. ``HEAD`` — git-status-style. In particular this keeps no-remote
       repos from falling through to the empty tree, which would list
       every tracked file as an addition.

    The caller has already handled repos without commits, so this only
    falls back to the empty tree for an unborn/orphan HEAD (matching the
    explicit-``HEAD``-override behavior: untracked files render as
    additions).
    """
    head_sha = _rev_parse(repo_dir, "HEAD")
    current_branch = _get_current_branch(repo_dir)

    if current_branch is not None:
        upstream_ref = f"origin/{current_branch}"
        upstream_sha = _rev_parse(repo_dir, upstream_ref)
        if upstream_sha is not None:
            if upstream_sha == head_sha and not _has_tracked_changes(repo_dir):
                logger.debug(
                    f"{upstream_ref} points at HEAD with a clean tree; "
                    "skipping it so committed work stays visible"
                )
            else:
                logger.debug(f"Using upstream of current branch: {upstream_ref}")
                return upstream_sha

    default_branch = _get_remote_default_branch(repo_dir)
    if default_branch is not None:
        merge_base = _merge_base(repo_dir, "HEAD", f"origin/{default_branch}")
        if merge_base is not None:
            logger.debug(f"Using merge base with origin/{default_branch}")
            return merge_base
        default_sha = _rev_parse(repo_dir, f"origin/{default_branch}")
        if default_sha is not None:
            logger.debug(f"Using default branch tip: origin/{default_branch}")
            return default_sha
    else:
        for local_default in ("main", "master"):
            local_default_sha = _rev_parse(repo_dir, local_default)
            if local_default_sha is not None:
                if local_default != current_branch:
                    merge_base = _merge_base(repo_dir, "HEAD", local_default)
                    if merge_base is not None and merge_base == local_default_sha:
                        logger.debug(f"Using local default branch: {local_default}")
                        return merge_base
                break

    if head_sha is not None:
        logger.debug("Using HEAD as display base (git status-style diff)")
        return head_sha
    logger.debug(f"Using empty tree reference: {GIT_EMPTY_TREE_HASH}")
    return GIT_EMPTY_TREE_HASH


def get_valid_ref(
    repo_dir: str | Path,
    override: str | None = None,
    *,
    purpose: Literal["export", "display"] = "export",
) -> str | None:
    """Get a valid git reference to compare against.

    If ``override`` is provided, it is resolved via ``git rev-parse --verify``
    and returned. This lets callers request, for example, ``HEAD`` to get
    ``git status``-style diffs against the latest commit instead of against
    the remote branch.

    The ``"HEAD"`` override is treated specially: if it does not resolve
    (no commits on the current branch — e.g. a freshly ``git init``'d
    workspace, or an orphan branch in a repo that has commits elsewhere),
    we fall back to the empty-tree hash so callers see untracked files as
    additions instead of an opaque ``rev-parse --verify`` failure. Other
    overrides that do not resolve still raise ``GitCommandError`` so a
    typo'd branch/SHA is not silently swallowed.

    Otherwise, auto-detects a reference according to ``purpose``. For
    ``purpose="export"`` (the default), tries multiple strategies:
    1. Current branch's origin (e.g., origin/main)
    2. Default branch (e.g., origin/main, origin/master)
    3. Merge base with default branch
    4. Empty tree (for new repositories)

    Args:
        repo_dir: Path to the git repository
        override: Optional explicit ref (e.g. ``"HEAD"`` or a commit hash) to
            use instead of the auto-detected comparison ref.
        purpose: What the ref is used for. ``"export"`` (default) only picks
            bases that are reconstructable from a fresh clone — required by
            workspace export/restore, whose patches are replayed elsewhere.
            ``"display"`` optimizes for showing a conversation's work (see
            :func:`_get_display_base_ref`): it skips a fully-pushed branch's
            own upstream, prefers the fork point over the default-branch
            tip, and degrades to ``HEAD`` (git status-style) instead of the
            empty tree for repos without a usable origin.

    Returns:
        Valid git reference hash, or None if no valid reference found

    Raises:
        GitCommandError: If a non-``"HEAD"`` ``override`` is provided and
            does not resolve.
    """
    if override is not None:
        try:
            # Resolve explicit override and surface failure to the caller so
            # the difference between "ref not found" and "no changes" stays
            # visible.
            return run_git_command(
                [
                    "git",
                    "--no-pager",
                    "rev-parse",
                    "--verify",
                    f"{override}^{{commit}}",
                ],
                repo_dir,
            )
        except GitCommandError:
            # ``HEAD`` is the canonical "current branch tip"; if it doesn't
            # resolve, the current branch has no commits yet. That happens for
            # freshly ``git init``'d workspaces *and* for orphan branches in
            # repos that have commits on other branches (so ``_repo_has_commits``
            # alone can't catch the latter). Treat both as empty-tree compares
            # so the Changes tab renders working-tree additions instead of
            # bubbling up an opaque ``rev-parse --verify`` failure to the GUI.
            #
            # For non-``HEAD`` overrides (explicit branches/SHAs the caller
            # asked for), keep the strict behavior so a typo doesn't silently
            # become "no changes".
            if override == "HEAD":
                logger.debug(
                    "Override 'HEAD' did not resolve in %s; using empty tree",
                    repo_dir,
                )
                return GIT_EMPTY_TREE_HASH
            raise

    # Check if repo has any commits first. Empty repos (created with git init)
    # won't have commits or remotes, so we can skip directly to the empty tree fallback.
    if not _repo_has_commits(repo_dir):
        logger.debug("Repository has no commits yet, using empty tree reference")
        return GIT_EMPTY_TREE_HASH

    if purpose == "display":
        return _get_display_base_ref(repo_dir)

    refs_to_try = []

    # Try current branch's origin
    try:
        current_branch = run_git_command(
            ["git", "--no-pager", "rev-parse", "--abbrev-ref", "HEAD"], repo_dir
        )
        if current_branch and current_branch != "HEAD":  # Not in detached HEAD state
            refs_to_try.append(f"origin/{current_branch}")
            logger.debug(f"Added current branch reference: origin/{current_branch}")
    except GitCommandError:
        logger.debug("Could not get current branch name")

    # Try to get default branch from remote
    try:
        remote_info = run_git_command(
            ["git", "--no-pager", "remote", "show", "origin"], repo_dir
        )
        for line in remote_info.splitlines():
            if "HEAD branch:" in line:
                default_branch = line.split(":")[-1].strip()
                if default_branch:
                    refs_to_try.append(f"origin/{default_branch}")
                    logger.debug(
                        f"Added default branch reference: origin/{default_branch}"
                    )

                    # Also try merge base with default branch
                    try:
                        merge_base = run_git_command(
                            [
                                "git",
                                "--no-pager",
                                "merge-base",
                                "HEAD",
                                f"origin/{default_branch}",
                            ],
                            repo_dir,
                        )
                        if merge_base:
                            refs_to_try.append(merge_base)
                            logger.debug(f"Added merge base reference: {merge_base}")
                    except GitCommandError:
                        logger.debug("Could not get merge base")
                break
    except GitCommandError:
        logger.debug("Could not get remote information")

    # Find the first valid reference
    for ref in refs_to_try:
        try:
            result = run_git_command(
                ["git", "--no-pager", "rev-parse", "--verify", ref], repo_dir
            )
            if result:
                logger.debug(f"Using valid reference: {ref} -> {result}")
                return result
        except GitCommandError:
            logger.debug(f"Reference not valid: {ref}")
            continue

    # Fallback to empty tree hash (always valid, no verification needed)
    logger.debug(f"Using empty tree reference: {GIT_EMPTY_TREE_HASH}")
    return GIT_EMPTY_TREE_HASH


def validate_git_repository(repo_dir: str | Path) -> Path:
    """Validate that the given directory is a git repository.

    Args:
        repo_dir: Path to check

    Returns:
        Validated Path object

    Raises:
        GitRepositoryError: If not a valid git repository
    """
    repo_path = Path(repo_dir).resolve()

    if not repo_path.exists():
        raise GitRepositoryError(f"Directory does not exist: {repo_path}")

    if not repo_path.is_dir():
        raise GitRepositoryError(f"Path is not a directory: {repo_path}")

    try:
        run_git_command(["git", "rev-parse", "--git-dir"], repo_path)
    except GitCommandError as e:
        raise GitRepositoryError(f"Not a git repository: {repo_path}") from e

    return repo_path


# ============================================================================
# Git URL utilities
# ============================================================================


def is_git_url(source: str) -> bool:
    """Check if a source string looks like a git URL.

    Detects git URLs by their protocol/scheme rather than enumerating providers.
    This handles any git hosting service (GitHub, GitLab, Codeberg, self-hosted, etc.)

    Args:
        source: String to check.

    Returns:
        True if the string appears to be a git URL, False otherwise.

    Examples:
        >>> is_git_url("https://github.com/owner/repo.git")
        True
        >>> is_git_url("git@github.com:owner/repo.git")
        True
        >>> is_git_url("/local/path")
        False
    """
    # HTTPS/HTTP URLs to git repositories
    if source.startswith(("https://", "http://")):
        return True

    # SSH format: git@host:path or user@host:path
    if re.match(r"^[\w.-]+@[\w.-]+:", source):
        return True

    # Git protocol
    if source.startswith("git://"):
        return True

    # File protocol (for testing)
    if source.startswith("file://"):
        return True

    return False


def normalize_git_url(url: str) -> str:
    """Normalize a git URL by ensuring .git suffix for HTTPS URLs.

    Args:
        url: Git URL to normalize.

    Returns:
        Normalized URL with .git suffix for HTTPS/HTTP URLs.

    Examples:
        >>> normalize_git_url("https://github.com/owner/repo")
        "https://github.com/owner/repo.git"
        >>> normalize_git_url("https://github.com/owner/repo.git")
        "https://github.com/owner/repo.git"
        >>> normalize_git_url("git@github.com:owner/repo.git")
        "git@github.com:owner/repo.git"
    """
    if url.startswith(("https://", "http://")) and not url.endswith(".git"):
        url = url.rstrip("/")
        url = f"{url}.git"
    return url


def extract_repo_name(source: str) -> str:
    """Extract a human-readable repository name from a git URL or path.

    Extracts the last path component (repo name) and sanitizes it for use
    in directory names or display purposes.

    Args:
        source: Git URL or local path string.

    Returns:
        A sanitized name suitable for use in directory names (max 32 chars).

    Examples:
        >>> extract_repo_name("https://github.com/owner/my-repo.git")
        "my-repo"
        >>> extract_repo_name("git@github.com:owner/my-repo.git")
        "my-repo"
        >>> extract_repo_name("/path/to/local-repo")
        "local-repo"
    """
    # Strip common prefixes to get to the path portion
    name = source
    for prefix in ("github:", "https://", "http://", "git://", "file://"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break

    # Handle SSH format: user@host:path -> path
    if "@" in name and ":" in name and "/" not in name.split(":")[0]:
        name = name.split(":", 1)[1]

    # Remove .git suffix and get last path component
    name = name.rstrip("/").removesuffix(".git")
    name = name.rsplit("/", 1)[-1]

    # Sanitize: keep alphanumeric, dash, underscore only
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")

    return name[:32] if name else "repo"


_REPO_ROOT_SKIP_DIRS = frozenset(
    {"node_modules", "venv", ".venv", "site-packages", "vendor"}
)


def resolve_git_repo_root(base: str | Path, max_depth: int = 3) -> Path | None:
    """Find the repository containing ``base`` or uniquely nested beneath it."""
    root = Path(base)
    for candidate in (root, *root.parents):
        if (candidate / ".git").exists():
            return candidate
    found: list[Path] = []
    frontier: list[tuple[Path, int]] = [(root, 0)]
    while frontier:
        current, depth = frontier.pop()
        if depth >= max_depth:
            continue
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name.startswith(".") or child.name in _REPO_ROOT_SKIP_DIRS:
                continue
            try:
                if child.is_symlink() or not child.is_dir():
                    continue
            except OSError:
                continue
            if (child / ".git").exists():
                found.append(child)
                if len(found) > 1:
                    return None  # ambiguous — don't guess which repo
            else:
                frontier.append((child, depth + 1))
    return found[0] if len(found) == 1 else None


def _split_git_remote(remote_url: str) -> tuple[str, list[str]] | None:
    url = remote_url.strip()
    scp = re.fullmatch(r"[\w.-]+@(?P<host>[\w.-]+):(?P<path>.+)", url)
    if scp:
        host = scp.group("host")
        path = scp.group("path")
    elif "://" in url:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        path = parsed.path
    else:
        host, separator, path = url.partition("/")
        if not separator or "." not in host:
            return None

    if not host:
        return None
    path = path.split("?", 1)[0].split("#", 1)[0]
    parts = [unquote(part) for part in path.strip("/").split("/") if part]
    if parts:
        parts[-1] = parts[-1].removesuffix(".git")
    if parts and not parts[-1]:
        parts.pop()
    return host.lower(), parts


def _provider_for_remote(host: str, parts: list[str]) -> str | None:
    if host in {"dev.azure.com", "ssh.dev.azure.com"} or host.endswith(
        ".visualstudio.com"
    ):
        return "azure_devops"
    if host == "bitbucket.org":
        return "bitbucket"
    if host == "codeberg.org" or "forgejo" in host:
        return "forgejo"
    for provider in ("github", "gitlab"):
        if provider in host:
            return provider
    if parts and parts[0].lower() == "scm":
        return "bitbucket_data_center"
    if "bitbucket" in host:
        return "bitbucket"
    return None


def _canonical_repo_parts(
    host: str, parts: list[str], provider: str | None
) -> list[str]:
    if provider == "bitbucket_data_center" and parts:
        normalized = parts[1:]
        if normalized:
            normalized[0] = normalized[0].upper()
        return normalized
    if provider != "azure_devops":
        return parts

    normalized = parts[1:] if parts and parts[0].lower() == "v3" else parts[:]
    for index, part in enumerate(normalized):
        if part.lower() == "_git":
            normalized.pop(index)
            break
    if host.endswith(".visualstudio.com") and not host.startswith("vs-ssh."):
        organization = host.removesuffix(".visualstudio.com").split(".")[0]
        if organization and normalized and normalized[0].lower() != organization:
            normalized.insert(0, organization)
    return normalized


def _repo_slug_and_provider(remote_url: str) -> tuple[str | None, str | None]:
    """Parse a canonical repository slug and provider from a remote URL."""
    split = _split_git_remote(remote_url)
    if split is None:
        return None, None
    host, parts = split
    provider = _provider_for_remote(host, parts)
    parts = _canonical_repo_parts(host, parts, provider)
    slug = "/".join(parts) if len(parts) >= 2 else None
    return slug, provider


def resolve_repo_identity(base: str | Path) -> dict[str, str]:
    """Resolve trace metadata for the repository associated with ``base``."""
    try:
        root = resolve_git_repo_root(base)
    except Exception:
        return {}
    if root is None:
        return {}

    metadata = get_git_repository_metadata(root, timeout=5)
    remote = metadata.get("repo_remote")
    if not remote:
        return {}
    slug, provider = _repo_slug_and_provider(remote)
    if not slug:
        return {}

    identity: dict[str, str] = {"repo": slug}
    if provider:
        identity["git_provider"] = provider
    if (branch := metadata.get("branch")) and branch != "DETACHED":
        identity["branch"] = branch
    if commit := metadata.get("head_commit"):
        identity["commit"] = commit
    return identity
