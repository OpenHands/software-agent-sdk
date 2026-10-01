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


class StorageAdapter(Protocol):
    """How one runtime mode stores conversations on this host."""

    @property
    def root(self) -> Path:
        """Where the runtimes live; the trash goes here, on the same filesystem."""
        ...

    def runtime(self, conversation_id: UUID) -> StoredRuntime | None: ...

    def runtimes(self) -> list[StoredRuntime]:
        """Every stored runtime, least recently active first."""
        ...

    def idle(self, conversation_id: UUID) -> AbstractAsyncContextManager[bool]:
        """Hold the runtime still; True if nothing runs or starts in it."""
        ...

    def leftovers(self) -> list[Path]:
        """Paths an earlier version set aside for deletion and never removed."""
        ...


class Reclaimer:
    def __init__(self, adapter: StorageAdapter) -> None:
        self.adapter = adapter
        self.trash = Trash(adapter.root)

    async def start(self) -> None:
        """Only safe before any runtime starts: every one is reclaimed."""
        for path in await asyncio.to_thread(self.adapter.leftovers):
            self.trash.discard(path)
        for runtime in await asyncio.to_thread(self.adapter.runtimes):
            await self.reclaim(runtime, Tier.CACHES)
        self.trash.empty_soon()

    async def shutdown(self) -> None:
        await self.trash.close()

    async def on_stop(self, conversation_id: UUID) -> None:
        runtime = await asyncio.to_thread(self.adapter.runtime, conversation_id)
        if runtime is not None and await self.reclaim(runtime, Tier.CACHES):
            self.trash.empty_soon()

    async def reclaim(self, runtime: StoredRuntime, tier: Tier) -> bool:
        """Discard what ``tier`` allows, unless the runtime is in use."""
        paths = await asyncio.to_thread(_select, runtime, tier)
        if not paths:
            return False
        async with self.adapter.idle(runtime.id) as idle:
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
