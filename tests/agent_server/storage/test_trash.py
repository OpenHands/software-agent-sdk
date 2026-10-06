import asyncio
import threading
from pathlib import Path

import pytest

from openhands.agent_server.storage import Trash, trash as trash_module


def entries(trash: Trash) -> list[Path]:
    return list(trash.dir.iterdir()) if trash.dir.exists() else []


@pytest.mark.asyncio
async def test_discard_moves_aside_then_empty_deletes(tmp_path):
    trash = Trash(tmp_path)
    target = tmp_path / "node_modules" / "left-pad"
    target.mkdir(parents=True)

    assert trash.discard(tmp_path / "node_modules")
    assert not (tmp_path / "node_modules").exists()
    assert len(entries(trash)) == 1

    trash.empty_soon()
    await trash.drain()
    assert entries(trash) == []


def test_discarding_a_missing_path_is_a_no_op(tmp_path):
    assert not Trash(tmp_path).discard(tmp_path / "gone")


@pytest.mark.asyncio
async def test_symlink_is_unlinked_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("keep")
    link = tmp_path / "root" / ".cache"
    link.parent.mkdir()
    link.symlink_to(outside, target_is_directory=True)
    trash = Trash(tmp_path / "root")

    assert trash.discard(link)
    trash.empty_soon()
    await trash.drain()

    assert (outside / "precious").read_text() == "keep"
    assert entries(trash) == []


@pytest.mark.asyncio
async def test_close_abandons_and_a_new_trash_finishes(tmp_path, monkeypatch):
    real_remove = trash_module._remove
    started, release = threading.Event(), threading.Event()

    def slow_remove(path: Path) -> None:
        started.set()
        assert release.wait(5)
        real_remove(path)

    monkeypatch.setattr(trash_module, "_remove", slow_remove)
    trash = Trash(tmp_path)
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        trash.discard(tmp_path / name)

    trash.empty_soon()
    await asyncio.to_thread(started.wait, 5)
    await trash.close()
    release.set()
    await asyncio.sleep(0.1)
    # The deletion in flight finishes in its thread; the queued one is left.
    assert len(entries(trash)) == 1

    monkeypatch.setattr(trash_module, "_remove", real_remove)
    restarted = Trash(tmp_path)
    restarted.empty_soon()
    await restarted.drain()
    assert entries(restarted) == []


@pytest.mark.asyncio
async def test_a_failing_entry_does_not_spin(tmp_path, monkeypatch):
    calls: list[Path] = []

    def fail(path: Path) -> None:
        calls.append(path)
        raise OSError("busy")

    monkeypatch.setattr(trash_module, "_remove", fail)
    trash = Trash(tmp_path)
    (tmp_path / "a").mkdir()
    trash.discard(tmp_path / "a")

    trash.empty_soon()
    await trash.drain()

    assert len(calls) == 1
    assert len(entries(trash)) == 1


@pytest.mark.asyncio
async def test_undeletable_entry_is_tried_once_and_others_still_go(
    tmp_path, monkeypatch
):
    # safe_rmtree swallows permission errors and returns False.
    real = trash_module.safe_rmtree
    stuck = tmp_path / ".trash"
    tried: list[Path] = []

    def flaky(path, description):
        tried.append(Path(path))
        return False if len(tried) == 1 else real(path, description)

    monkeypatch.setattr(trash_module, "safe_rmtree", flaky)
    trash = Trash(tmp_path)
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        trash.discard(tmp_path / name)

    trash.empty_soon()
    await trash.drain()

    assert len(tried) == 2
    assert len(entries(trash)) == 1
    assert all(path.parent == stuck for path in tried)
