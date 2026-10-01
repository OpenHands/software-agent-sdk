"""The reclaimer against a minimal adapter: nothing here knows about Docker."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from openhands.agent_server.storage import Reclaimer, StoredRuntime


class FakeAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.homes: dict[UUID, Path] = {}
        self.busy: set[UUID] = set()
        self.stale: list[Path] = []

    def add(self, *, busy: bool = False) -> tuple[UUID, Path]:
        conversation_id = uuid4()
        home = self.root / conversation_id.hex / "home"
        (home / ".cache" / "uv").mkdir(parents=True)
        (home / ".npm").mkdir()
        (home / ".bash_history").write_text("ls")
        self.homes[conversation_id] = home
        if busy:
            self.busy.add(conversation_id)
        return conversation_id, home

    def runtime(self, conversation_id: UUID) -> StoredRuntime | None:
        home = self.homes.get(conversation_id)
        return StoredRuntime(conversation_id, 0.0, home=home) if home else None

    def runtimes(self) -> list[StoredRuntime]:
        return [r for cid in self.homes if (r := self.runtime(cid)) is not None]

    @asynccontextmanager
    async def idle(self, conversation_id: UUID) -> AsyncIterator[bool]:
        yield conversation_id not in self.busy

    def leftovers(self) -> list[Path]:
        return self.stale

    def retire(self, conversation_id: UUID) -> list[Path]:
        return []

    async def on_retired(self, conversation_id: UUID) -> None:
        pass


@pytest.mark.asyncio
async def test_on_stop_drops_caches_and_keeps_the_rest(tmp_path):
    adapter = FakeAdapter(tmp_path)
    conversation_id, home = adapter.add()
    reclaimer = Reclaimer(adapter)

    await reclaimer.on_stop(conversation_id)
    await reclaimer.trash.drain()

    assert not (home / ".cache").exists() and not (home / ".npm").exists()
    assert (home / ".bash_history").read_text() == "ls"


@pytest.mark.asyncio
async def test_busy_runtime_is_left_alone(tmp_path):
    adapter = FakeAdapter(tmp_path)
    conversation_id, home = adapter.add(busy=True)
    reclaimer = Reclaimer(adapter)

    await reclaimer.on_stop(conversation_id)
    await reclaimer.start()
    await reclaimer.trash.drain()

    assert (home / ".cache").is_dir()


@pytest.mark.asyncio
async def test_start_reclaims_every_idle_runtime_and_old_leftovers(tmp_path):
    adapter = FakeAdapter(tmp_path)
    homes = [adapter.add()[1] for _ in range(3)]
    stale = tmp_path / "old-build-leftover"
    stale.mkdir()
    adapter.stale = [stale]
    reclaimer = Reclaimer(adapter)

    await reclaimer.start()
    await reclaimer.trash.drain()

    assert not any((home / ".cache").exists() for home in homes)
    assert not stale.exists()
    assert list(reclaimer.trash.dir.iterdir()) == []


@pytest.mark.asyncio
async def test_unknown_runtime_is_a_no_op(tmp_path):
    reclaimer = Reclaimer(FakeAdapter(tmp_path))

    await reclaimer.on_stop(uuid4())

    assert not reclaimer.trash.dir.exists()
