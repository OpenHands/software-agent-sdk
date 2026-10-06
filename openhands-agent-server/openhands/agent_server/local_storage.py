"""Storage of host-local conversations: their git worktrees."""

import subprocess
from pathlib import Path
from uuid import UUID

from openhands.agent_server.utils import safe_rmtree
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.command import sanitized_env


logger = get_logger(__name__)


def remove_conversation_worktree(root: Path, conversation_id: UUID) -> None:
    """Remove a deleted conversation's worktrees; their branches stay."""
    conversation_dir = root / str(conversation_id)
    if conversation_dir.is_symlink() or not conversation_dir.is_dir():
        return
    repos = [
        repo for worktree in _worktrees(conversation_dir) if (repo := _repo(worktree))
    ]
    if not safe_rmtree(
        conversation_dir, f"worktrees of conversation {conversation_id}"
    ):
        return
    # Drops git's record of the missing worktree; the branch is untouched.
    for repo in repos:
        _git(repo, "worktree", "prune")


def _worktrees(conversation_dir: Path) -> list[Path]:
    # A worktree's .git is a file pointing at the main repository.
    return [
        child
        for child in conversation_dir.iterdir()
        if not child.is_symlink() and (child / ".git").is_file()
    ]


def _repo(worktree: Path) -> Path | None:
    common = _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common) if common else None


def _git(cwd: Path, *args: str) -> str | None:
    result = subprocess.run(
        # The repo's config is agent-writable; fsmonitor would run it.
        [
            "git",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(cwd),
            *args,
        ],
        env=sanitized_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        logger.warning("git %s failed in %s: %s", " ".join(args), cwd, result.stderr)
        return None
    return result.stdout.strip()
