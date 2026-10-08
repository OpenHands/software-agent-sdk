"""What each tier may drop: paths a stopped runtime can rebuild."""

import os
import subprocess
from pathlib import Path
from typing import Final

from openhands.sdk.utils.command import sanitized_env


# Rebuildable caches under $HOME: XDG tools (uv, pip, yarn, go) use .cache,
# npm ignores XDG and uses .npm.
CACHE_DIRS: Final[tuple[str, ...]] = (".cache", ".npm")


def caches(home: Path) -> list[Path]:
    # lexists: a symlinked cache is still discarded (unlinked, never followed).
    return [home / name for name in CACHE_DIRS if os.path.lexists(home / name)]


def ignored_dirs(workspace: Path) -> list[Path]:
    """Git-ignored dirs of a workspace and of the checkouts directly inside it."""
    workspace = workspace.resolve()
    repos = [workspace, *(git.parent for git in workspace.glob("*/.git"))]
    return [
        path
        for repo in repos
        if (repo / ".git").exists()
        for path in git_ignored_dirs(repo)
        if path.parent.resolve().is_relative_to(workspace)
    ]


def git_ignored_dirs(repo: Path) -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            # The repo's own config is agent-writable; fsmonitor would run it.
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            "ls-files",
            "-z",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
        ],
        env=sanitized_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        return []
    candidates = sorted(
        path
        for entry in result.stdout.split("\0")
        if entry.endswith("/")
        and (path := repo / entry.rstrip("/")).is_dir()
        and not path.is_symlink()
        and not _holds_checkout(path)
    )
    # A `dir/**/*` pattern lists dir/ and each of its subdirs; shedding the
    # parent already takes the rest.
    kept: list[Path] = []
    for path in candidates:
        if not any(path.is_relative_to(parent) for parent in kept):
            kept.append(path)
    return kept


def _holds_checkout(path: Path) -> bool:
    """An ignored dir with a clone in it is someone's work, not build output.

    Bounded to three levels so a large node_modules is not walked.
    """
    return any(
        next(path.glob(pattern), None) is not None
        for pattern in (".git", "*/.git", "*/*/.git")
    )
