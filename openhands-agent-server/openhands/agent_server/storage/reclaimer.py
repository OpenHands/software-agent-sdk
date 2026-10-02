import asyncio
import shutil
import time
from contextlib import AbstractAsyncContextManager, suppress
from pathlib import Path
from typing import Final, Protocol
from uuid import UUID

from openhands.agent_server.storage.model import StoredRuntime, Tier
from openhands.agent_server.storage.selectors import caches, ignored_dirs
from openhands.agent_server.storage.trash import Trash
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Seconds between passes that apply the disk budget and retention.
MAINTENANCE_INTERVAL: Final[float] = 300.0


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

    def retire(self, conversation_id: UUID) -> list[Path]:
        """Record the runtime as retired; return what to discard. Called idle."""
        ...

    async def on_retired(self, conversation_id: UUID) -> None: ...


class Reclaimer:
    """Frees what a runtime mode stores per conversation, by tier.

    Files are only moved into the trash, and only while the storage holds the
    runtime idle; the trash deletes them in the background.
    """

    def __init__(
        self,
        storage: ReclaimableStorage,
        *,
        disk_budget: float | None = None,
        retention_days: float | None = None,
    ) -> None:
        self.storage = storage
        self.disk_budget = disk_budget
        self.retention_days = retention_days
        self.trash = Trash(storage.root)
        self._maintenance: asyncio.Task[None] | None = None
        self._over_budget_warned = False

    async def start(self) -> None:
        """Only safe before any runtime starts: every one is reclaimed."""
        for runtime in await asyncio.to_thread(self.storage.runtimes):
            await self.reclaim(runtime, Tier.CACHES)
        self.trash.empty_soon()
        if self.disk_budget or self.retention_days:
            self._maintenance = asyncio.create_task(self._maintenance_loop())

    async def shutdown(self) -> None:
        if self._maintenance is not None:
            self._maintenance.cancel()
            with suppress(asyncio.CancelledError):
                await self._maintenance
            self._maintenance = None
        await self.trash.close()

    async def run_pass(self) -> None:
        """Apply the configured policies once; log only if something was freed."""
        free_before = _free_bytes(self.storage.root)
        # Retention first: what it retires no longer counts against the budget.
        retired = await self._enforce_retention()
        shed = await self._enforce_disk_budget()
        if not (retired or shed):
            return
        await self.trash.drain()
        logger.info(
            "Conversation storage: freed %.1f GB (retired %d runtimes, shed "
            "dependencies of %d), %s at %.0f%%",
            # Other writes on the filesystem can outweigh a small reclaim.
            max(0, _free_bytes(self.storage.root) - free_before) / 1e9,
            retired,
            shed,
            self.storage.root,
            disk_usage(self.storage.root) * 100,
        )

    async def on_stop(self, conversation_id: UUID) -> None:
        runtime = await asyncio.to_thread(self.storage.runtime, conversation_id)
        if runtime is not None and await self.reclaim(runtime, Tier.CACHES):
            self.trash.empty_soon()

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(MAINTENANCE_INTERVAL)
            try:
                await self.run_pass()
            except Exception:
                logger.exception("error_reclaiming_conversation_storage")

    async def _enforce_retention(self) -> int:
        """Retire runtimes inactive for longer than the retention period."""
        days = self.retention_days
        if not days:
            return 0
        cutoff = time.time() - days * 86400
        retired = 0
        for runtime in await asyncio.to_thread(self.storage.runtimes):
            if runtime.last_active > cutoff:
                break
            if await self.reclaim(runtime, Tier.RUNTIME):
                retired += 1
        if retired:
            self.trash.empty_soon()
        return retired

    async def _enforce_disk_budget(self) -> int:
        """Shed dependencies of stopped runtimes, oldest first, until under budget."""
        budget = self.disk_budget
        root = self.storage.root
        if not budget or disk_usage(root) <= budget:
            self._over_budget_warned = False
            return 0
        shed = 0
        for runtime in await asyncio.to_thread(self.storage.runtimes):
            if not await self.reclaim(runtime, Tier.DEPENDENCIES):
                continue
            shed += 1
            # Usage only drops once the trash is actually emptied.
            self.trash.empty_soon()
            await self.trash.drain()
            if disk_usage(root) <= budget:
                break
        if not shed and not self._over_budget_warned:
            self._over_budget_warned = True
            logger.warning(
                "Conversation storage at %s is above its %.0f%% budget with "
                "nothing left to shed from stopped runtimes",
                root,
                budget * 100,
            )
        return shed

    async def reclaim(self, runtime: StoredRuntime, tier: Tier) -> bool:
        """Discard what ``tier`` allows, unless the runtime is in use."""
        retiring = tier >= Tier.RUNTIME
        # Retiring takes the whole runtime, so there is nothing to select.
        paths = [] if retiring else await asyncio.to_thread(_select, runtime, tier)
        if not (paths or retiring):
            return False
        async with self.storage.idle(runtime.id) as idle:
            if not idle:
                return False
            if retiring:
                paths = self.storage.retire(runtime.id)
            # Only renames here: the lock is held for microseconds.
            moved = [path for path in paths if self.trash.discard(path)]
        if retiring and moved:
            await self.storage.on_retired(runtime.id)
        return bool(moved)


def _select(runtime: StoredRuntime, tier: Tier) -> list[Path]:
    paths: list[Path] = []
    if tier >= Tier.CACHES and runtime.home is not None:
        paths += caches(runtime.home)
    if tier >= Tier.DEPENDENCIES:
        for workspace in runtime.workspaces:
            paths += ignored_dirs(workspace)
    return paths


def disk_usage(path: Path) -> float:
    usage = shutil.disk_usage(_existing(path))
    return usage.used / (usage.used + usage.free)


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(_existing(path)).free


def _existing(path: Path) -> Path:
    # The root may not exist until the first runtime does; its nearest
    # existing parent is on the same filesystem.
    while not path.exists() and path != path.parent:
        path = path.parent
    return path
