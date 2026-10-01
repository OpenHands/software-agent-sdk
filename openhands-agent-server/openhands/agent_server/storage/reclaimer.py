import asyncio
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol
from uuid import UUID

from openhands.agent_server.storage.model import StoredRuntime, Tier
from openhands.agent_server.storage.selectors import caches
from openhands.agent_server.storage.trash import Trash
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class ReclaimableStorage(Protocol):
    """How one runtime mode stores conversations on this host."""

    @property
    def root(self) -> Path:
        """Where the runtimes live; the trash goes here, on the same filesystem."""
        ...

    def runtime(self, conversation_id: UUID) -> StoredRuntime | None:
        """The conversation's stored runtime, or None if it has none here."""
        ...

    def runtimes(self) -> list[StoredRuntime]:
        """Every stored runtime, least recently active first."""
        ...

    def idle(self, conversation_id: UUID) -> AbstractAsyncContextManager[bool]:
        """Hold the runtime still; True if nothing runs or starts in it."""
        ...


class Reclaimer:
    """Frees what a runtime mode stores per conversation, by tier.

    Files are only moved into the trash, and only while the storage holds the
    runtime idle; the trash deletes them in the background.
    """

    def __init__(self, storage: ReclaimableStorage) -> None:
        self.storage = storage
        self.trash = Trash(storage.root)

    async def start(self) -> None:
        """Only safe before any runtime starts: every one is reclaimed."""
        for runtime in await asyncio.to_thread(self.storage.runtimes):
            await self.reclaim(runtime, Tier.CACHES)
        self.trash.empty_soon()

    async def shutdown(self) -> None:
        await self.trash.close()

    async def on_stop(self, conversation_id: UUID) -> None:
        runtime = await asyncio.to_thread(self.storage.runtime, conversation_id)
        if runtime is not None and await self.reclaim(runtime, Tier.CACHES):
            self.trash.empty_soon()

    async def reclaim(self, runtime: StoredRuntime, tier: Tier) -> bool:
        """Discard what ``tier`` allows, unless the runtime is in use."""
        paths = await asyncio.to_thread(_select, runtime, tier)
        if not paths:
            return False
        async with self.storage.idle(runtime.id) as idle:
            if not idle:
                return False
            # Only renames here: the lock is held for microseconds.
            moved = [path for path in paths if self.trash.discard(path)]
        return bool(moved)


def _select(runtime: StoredRuntime, tier: Tier) -> list[Path]:
    paths: list[Path] = []
    if tier >= Tier.CACHES and runtime.home is not None:
        paths += caches(runtime.home)
    return paths
