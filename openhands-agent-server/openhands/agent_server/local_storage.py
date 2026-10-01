"""Storage of host-local conversations: their git worktrees."""

import subprocess
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from openhands.agent_server.storage import StoredRuntime
from openhands.agent_server.utils import safe_rmtree
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.command import sanitized_env


if TYPE_CHECKING:
    from openhands.agent_server.conversation_service import ConversationService


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LocalWorktreeAdapter:
    """``<conversation_worktree_root>/<id>/<repo>``: one worktree per conversation.

    The worktree's branch lives in the user's repository and may hold
    unmerged agent commits, so only the checkout is ever removed.
    """

    # Only needed to tell whether a conversation is loaded.
    service: "ConversationService"
    worktree_root: Path
    conversations_dir: Path

    @property
    def root(self) -> Path:
        return self.worktree_root.resolve()

    def runtime(self, conversation_id: UUID) -> StoredRuntime | None:
        conversation_dir = self.root / str(conversation_id)
        if conversation_dir.is_symlink() or not conversation_dir.is_dir():
            return None
        state = self.conversations_dir / conversation_id.hex / "base_state.json"
        try:
            last_active = state.stat().st_mtime
        except OSError:
            last_active = conversation_dir.stat().st_mtime
        # No home: host-local conversations share the host's $HOME.
        return StoredRuntime(
            conversation_id,
            last_active,
            workspaces=tuple(_worktrees(conversation_dir)),
        )

    def runtimes(self) -> list[StoredRuntime]:
        if not self.root.is_dir():
            return []
        runtimes = [
            runtime
            for conversation_dir in self.root.iterdir()
            if (conversation_id := _parse_id(conversation_dir.name))
            and (runtime := self.runtime(conversation_id))
        ]
        return sorted(runtimes, key=lambda runtime: runtime.last_active)

    def idle(self, conversation_id: UUID) -> AbstractAsyncContextManager[bool]:
        """True if the conversation is not loaded, so nothing runs in it.

        Holds the conversation's lifecycle lock, which loading it also takes.
        """
        return self.service.conversation_unloaded(conversation_id)

    def retire(self, conversation_id: UUID) -> list[Path]:  # noqa: ARG002 (protocol)
        # Not supported yet: a resumed local conversation would find no
        # workspace and nothing marks it read-only.
        return []

    async def on_retired(self, conversation_id: UUID) -> None:
        pass


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


def _parse_id(name: str) -> UUID | None:
    # Worktree dirs are named str(uuid), with dashes.
    with suppress(ValueError):
        return UUID(name)
    return None


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
