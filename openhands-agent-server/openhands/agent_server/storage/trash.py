import asyncio
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from openhands.agent_server.utils import safe_rmtree
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class Trash:
    """The only place stored files are deleted.

    ``discard`` is one rename, so it can run under a lock and races nothing: a
    reader sees the path whole or not at all. Deletion happens in the
    background, and whatever an interrupted run leaves is emptied on the next
    start. ``root`` must be on the same filesystem as everything discarded.
    """

    def __init__(self, root: Path) -> None:
        self.dir = root / ".trash"
        self._emptying: asyncio.Task[None] | None = None

    def discard(self, path: Path) -> bool:
        try:
            self.dir.mkdir(mode=0o700, exist_ok=True)
            path.rename(self.dir / uuid4().hex)
        except FileNotFoundError:
            return False
        except OSError:
            logger.warning("Failed to discard %s", path, exc_info=True)
            return False
        return True

    def empty_soon(self) -> None:
        if self._emptying is None or self._emptying.done():
            self._emptying = asyncio.create_task(self._empty())

    async def drain(self) -> None:
        """Wait until everything discarded so far is deleted."""
        while self._emptying is not None and not self._emptying.done():
            await asyncio.shield(self._emptying)

    async def close(self) -> None:
        """Abandon deletion; the next start picks up what is left."""
        if self._emptying is not None:
            self._emptying.cancel()
            await asyncio.gather(self._emptying, return_exceptions=True)
            self._emptying = None

    async def _empty(self) -> None:
        # Re-list for entries discarded meanwhile, but try each once per run:
        # one that cannot be deleted must not spin; the next start retries it.
        tried: set[Path] = set()
        while entries := [
            entry
            for entry in await asyncio.to_thread(self._entries)
            if entry not in tried
        ]:
            for entry in entries:
                tried.add(entry)
                try:
                    await asyncio.to_thread(_remove, entry)
                except Exception:
                    logger.warning("Failed to delete %s", entry, exc_info=True)

    def _entries(self) -> list[Path]:
        with suppress(FileNotFoundError):
            return list(self.dir.iterdir())
        return []


def _remove(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
    # safe_rmtree reports failure instead of raising.
    elif not safe_rmtree(path, "discarded conversation storage"):
        raise OSError(f"Could not delete {path}")
