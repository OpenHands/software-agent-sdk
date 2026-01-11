"""Plugin fetching utilities for remote plugin sources."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".openhands" / "cache" / "plugins"


class PluginFetchError(Exception):
    """Raised when fetching a plugin fails."""

    pass


def parse_plugin_source(source: str) -> tuple[str, str]:
    """Parse plugin source into (type, url).

    Args:
        source: Plugin source string. Can be:
            - "github:owner/repo" - GitHub repository shorthand
            - "https://github.com/owner/repo.git" - Full git URL
            - "git@github.com:owner/repo.git" - SSH git URL
            - "/local/path" - Local path

    Returns:
        Tuple of (source_type, normalized_url) where source_type is one of:
        - "github": GitHub repository
        - "git": Any git URL
        - "local": Local filesystem path

    Examples:
        >>> parse_plugin_source("github:owner/repo")
        ("github", "https://github.com/owner/repo.git")
        >>> parse_plugin_source("https://gitlab.com/org/repo.git")
        ("git", "https://gitlab.com/org/repo.git")
        >>> parse_plugin_source("/local/path")
        ("local", "/local/path")
    """
    source = source.strip()

    # GitHub shorthand: github:owner/repo
    if source.startswith("github:"):
        repo_path = source[7:]  # Remove "github:" prefix
        # Validate format
        if "/" not in repo_path or repo_path.count("/") > 1:
            raise PluginFetchError(
                f"Invalid GitHub shorthand format: {source}. "
                f"Expected format: github:owner/repo"
            )
        url = f"https://github.com/{repo_path}.git"
        return ("github", url)

    # Git URL patterns
    git_url_patterns = [
        r"^https?://.*\.git$",  # HTTPS with .git suffix
        r"^https?://github\.com/",  # GitHub HTTPS (may not have .git)
        r"^https?://gitlab\.com/",  # GitLab HTTPS
        r"^https?://bitbucket\.org/",  # Bitbucket HTTPS
        r"^git@.*:.*\.git$",  # SSH format
        r"^git://",  # Git protocol
    ]

    for pattern in git_url_patterns:
        if re.match(pattern, source):
            # Normalize: ensure .git suffix for HTTPS URLs
            url = source
            if url.startswith("https://") and not url.endswith(".git"):
                # Remove trailing slash if present
                url = url.rstrip("/")
                url = f"{url}.git"
            return ("git", url)

    # Local path
    if source.startswith("/") or source.startswith("~") or source.startswith("."):
        return ("local", source)

    # If it looks like a path (contains path separators but no URL scheme)
    if "/" in source and "://" not in source and not source.startswith("github:"):
        # Could be a relative path
        return ("local", source)

    raise PluginFetchError(
        f"Unable to parse plugin source: {source}. "
        f"Expected formats: 'github:owner/repo', git URL, or local path"
    )


def get_cache_path(source: str, cache_dir: Path | None = None) -> Path:
    """Get the cache path for a plugin source.

    Creates a deterministic path based on a hash of the source URL.

    Args:
        source: The plugin source (URL or path).
        cache_dir: Base cache directory. Defaults to ~/.openhands/cache/plugins/

    Returns:
        Path where the plugin should be cached.
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR

    # Create a hash of the source for the directory name
    source_hash = hashlib.sha256(source.encode()).hexdigest()[:16]

    # Also include a readable portion of the source
    # Extract repo name from various formats
    readable_name = _extract_readable_name(source)

    cache_name = f"{readable_name}-{source_hash}"
    return cache_dir / cache_name


def _extract_readable_name(source: str) -> str:
    """Extract a human-readable name from a source URL/path.

    Args:
        source: Plugin source string.

    Returns:
        A sanitized, readable name for the cache directory.
    """
    # Remove common prefixes and suffixes
    name = source

    # Handle github: prefix
    if name.startswith("github:"):
        name = name[7:]

    # Handle URLs
    if "://" in name:
        # Remove protocol
        name = name.split("://", 1)[1]

    # Handle SSH format (git@github.com:owner/repo.git)
    if name.startswith("git@"):
        name = name.split(":", 1)[1] if ":" in name else name

    # Remove .git suffix
    if name.endswith(".git"):
        name = name[:-4]

    # Get the last component (repo name)
    if "/" in name:
        parts = name.rstrip("/").split("/")
        # For owner/repo format, use repo name
        name = parts[-1] if parts else name

    # Sanitize: only allow alphanumeric, dash, underscore
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    name = re.sub(r"-+", "-", name)  # Collapse multiple dashes
    name = name.strip("-")

    # Limit length
    return name[:32] if name else "plugin"


def fetch_plugin(
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
) -> Path:
    """Fetch a plugin from a remote source and return the local cached path.

    Args:
        source: Plugin source - can be:
            - "github:owner/repo" - GitHub repository shorthand
            - "https://github.com/owner/repo.git" - Full git URL
            - "/local/path" - Local path (returned as-is)
        cache_dir: Directory for caching. Defaults to ~/.openhands/cache/plugins/
        ref: Optional branch, tag, or commit to checkout.
        update: If True and cache exists, update it. If False, use cached version as-is.

    Returns:
        Path to the local plugin directory (ready for Plugin.load())

    Raises:
        PluginFetchError: If fetching fails.
    """
    source_type, url = parse_plugin_source(source)

    # Local paths are returned as-is
    if source_type == "local":
        local_path = Path(url).expanduser().resolve()
        if not local_path.exists():
            raise PluginFetchError(f"Local plugin path does not exist: {local_path}")
        return local_path

    # Get cache path
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR

    plugin_path = get_cache_path(url, cache_dir)

    # Ensure cache directory exists
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        if plugin_path.exists() and (plugin_path / ".git").exists():
            if update:
                _update_repository(plugin_path, ref)
            else:
                logger.debug(f"Using cached plugin at {plugin_path}")
                if ref:
                    _checkout_ref(plugin_path, ref)
        else:
            _clone_repository(url, plugin_path, ref)

        return plugin_path

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else str(e)
        raise PluginFetchError(f"Git operation failed: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise PluginFetchError(f"Git operation timed out: {e}") from e
    except Exception as e:
        raise PluginFetchError(f"Failed to fetch plugin from {source}: {e}") from e


def _clone_repository(url: str, dest: Path, ref: str | None = None) -> None:
    """Clone a git repository.

    Args:
        url: Git URL to clone.
        dest: Destination path.
        ref: Optional branch/tag to checkout.
    """
    logger.info(f"Cloning plugin from {url}")

    # Remove existing directory if it exists (but isn't a valid git repo)
    if dest.exists():
        shutil.rmtree(dest)

    # Build clone command
    cmd = ["git", "clone", "--depth", "1"]

    if ref:
        cmd.extend(["--branch", ref])

    cmd.extend([url, str(dest)])

    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        timeout=120,
    )

    logger.debug(f"Plugin cloned to {dest}")


def _update_repository(repo_path: Path, ref: str | None = None) -> None:
    """Update an existing repository.

    Args:
        repo_path: Path to the repository.
        ref: Optional branch/tag to checkout.
    """
    logger.debug(f"Updating plugin repository at {repo_path}")

    try:
        # Fetch latest changes
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=repo_path,
            check=True,
            capture_output=True,
            timeout=60,
        )

        if ref:
            _checkout_ref(repo_path, ref)
        else:
            # Get the current branch
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            current_branch = result.stdout.strip()

            if current_branch and current_branch != "HEAD":
                # Reset to latest on current branch
                subprocess.run(
                    ["git", "reset", "--hard", f"origin/{current_branch}"],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )

        logger.debug("Plugin repository updated successfully")

    except subprocess.TimeoutExpired:
        logger.warning("Git update timed out, using existing cached repository")
    except subprocess.CalledProcessError as e:
        logger.warning(
            f"Failed to update repository: {e.stderr.decode() if e.stderr else e}, "
            f"using existing cached version"
        )


def _checkout_ref(repo_path: Path, ref: str) -> None:
    """Checkout a specific ref (branch, tag, or commit).

    Args:
        repo_path: Path to the repository.
        ref: Branch, tag, or commit to checkout.
    """
    logger.debug(f"Checking out ref: {ref}")

    # First try to fetch the ref
    try:
        subprocess.run(
            ["git", "fetch", "origin", ref],
            cwd=repo_path,
            capture_output=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass  # May fail for commits, that's ok

    # Checkout the ref
    subprocess.run(
        ["git", "checkout", ref],
        cwd=repo_path,
        check=True,
        capture_output=True,
        timeout=30,
    )

    # If it's a branch, reset to origin
    try:
        subprocess.run(
            ["git", "reset", "--hard", f"origin/{ref}"],
            cwd=repo_path,
            capture_output=True,
            timeout=30,
        )
    except subprocess.CalledProcessError:
        pass  # May fail for tags/commits, that's ok
