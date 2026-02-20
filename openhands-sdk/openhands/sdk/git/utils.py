import logging
import re
import shlex
import subprocess
from pathlib import Path

from openhands.sdk.git.exceptions import GitCommandError, GitRepositoryError


logger = logging.getLogger(__name__)

# Git empty tree hash - this is a well-known constant in git
# representing the hash of an empty tree object
GIT_EMPTY_TREE_HASH = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


# ============================================================================
# Credential redaction utilities (must be defined before run_git_command)
# ============================================================================


def redact_url_credentials(url: str) -> str:
    """Redact credentials from a git URL for safe logging and display.

    Replaces any username:password or token in the URL with "****" to prevent
    credential exposure in logs, error messages, and persisted state.

    Handles multiple URL formats:
    - HTTPS with embedded credentials: https://user:token@host/path
    - HTTPS with OAuth2: https://oauth2:token@host/path
    - HTTPS with x-token-auth: https://x-token-auth:token@host/path
    - SSH format (user@host:path) - no credentials to redact

    Args:
        url: Git URL that may contain credentials.

    Returns:
        URL with credentials redacted, or the original URL if no credentials found.

    Examples:
        >>> redact_url_credentials("https://oauth2:SECRET@gitlab.com/repo.git")
        "https://****@gitlab.com/repo.git"
        >>> redact_url_credentials("https://github.com/owner/repo.git")
        "https://github.com/owner/repo.git"
        >>> redact_url_credentials("git@github.com:owner/repo.git")
        "git@github.com:owner/repo.git"
    """
    # Match HTTPS/HTTP URLs with credentials: protocol://user:pass@host or protocol://user@host
    # The pattern captures: protocol, credentials (user:pass or just user), and the rest
    pattern = r"^(https?://)([^@/]+)@(.+)$"
    match = re.match(pattern, url)

    if match:
        protocol = match.group(1)
        rest = match.group(3)
        return f"{protocol}****@{rest}"

    return url


def _redact_args_for_logging(args: list[str]) -> str:
    """Redact credentials from git command arguments for safe logging.

    Joins command args with shlex.join and redacts any URLs containing credentials.

    Args:
        args: List of command arguments.

    Returns:
        A string representation of the command with credentials redacted.
    """
    redacted_args = [redact_url_credentials(arg) for arg in args]
    return shlex.join(redacted_args)


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
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

        if result.returncode != 0:
            # Redact credentials from command for logging and error messages
            cmd_str = _redact_args_for_logging(args)
            error_msg = f"Git command failed: {cmd_str}"
            logger.error(
                f"{error_msg}. Exit code: {result.returncode}. Stderr: {result.stderr}"
            )
            raise GitCommandError(
                message=error_msg,
                command=args,
                exit_code=result.returncode,
                stderr=result.stderr.strip(),
            )

        logger.debug(f"Git command succeeded: {_redact_args_for_logging(args)}")
        return result.stdout.strip()

    except subprocess.TimeoutExpired as e:
        cmd_str = _redact_args_for_logging(args)
        error_msg = f"Git command timed out: {cmd_str}"
        logger.error(error_msg)
        raise GitCommandError(
            message=error_msg,
            command=args,
            exit_code=-1,
            stderr="Command timed out",
        ) from e
    except FileNotFoundError as e:
        error_msg = "Git command not found. Is git installed?"
        logger.error(error_msg)
        raise GitCommandError(
            message=error_msg,
            command=args,
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


def get_valid_ref(repo_dir: str | Path) -> str | None:
    """Get a valid git reference to compare against.

    Tries multiple strategies to find a valid reference:
    1. Current branch's origin (e.g., origin/main)
    2. Default branch (e.g., origin/main, origin/master)
    3. Merge base with default branch
    4. Empty tree (for new repositories)

    Args:
        repo_dir: Path to the git repository

    Returns:
        Valid git reference hash, or None if no valid reference found
    """
    refs_to_try = []

    # Check if repo has any commits first. Empty repos (created with git init)
    # won't have commits or remotes, so we can skip directly to the empty tree fallback.
    if not _repo_has_commits(repo_dir):
        logger.debug("Repository has no commits yet, using empty tree reference")
        return GIT_EMPTY_TREE_HASH

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

    # Check if it's a git repository by looking for .git directory or file
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        # Maybe we're in a subdirectory, try to find the git root
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
