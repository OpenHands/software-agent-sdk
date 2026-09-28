import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

from openhands.sdk.io import LocalFileStore
from openhands.sdk.io.storage_safety import (
    StorageSafetyConfig,
    StorageSafetyController,
    StorageSafetyError,
    check_storage_safety_paths,
)


@pytest.fixture
def disk_space(monkeypatch):
    usage = SimpleNamespace(total=1_000_000, used=900_000, free=100_000)
    monkeypatch.setattr(
        "openhands.sdk.io.storage_safety.shutil.disk_usage", lambda _: usage
    )
    return usage


def test_admission_boundary_and_write_headroom(tmp_path, disk_space):
    config = StorageSafetyConfig()
    disk_space.free = 50_000
    check_storage_safety_paths([tmp_path / "not-created"], config)
    with pytest.raises(StorageSafetyError) as caught:
        check_storage_safety_paths([tmp_path], config, required_bytes=1)
    assert caught.value.to_dict()["shortage_bytes"] == 1
    assert caught.value.to_dict()["free_ratio"] == 0.05
    disk_space.free = 49_999
    with pytest.raises(StorageSafetyError, match="5%"):
        check_storage_safety_paths([tmp_path], config)


def test_unknown_capacity_fails_closed(tmp_path, disk_space):
    disk_space.total = 0
    with pytest.raises(StorageSafetyError) as caught:
        check_storage_safety_paths([tmp_path], StorageSafetyConfig())
    assert caught.value.code == "StorageCheckFailed"


def test_utf8_atomic_replacement_checks_full_temporary_size(tmp_path, disk_space):
    store = LocalFileStore(str(tmp_path))
    store.write("note", "old content stays intact")
    store.storage_safety = StorageSafetyController([tmp_path], StorageSafetyConfig())
    disk_space.free = 50_005
    with pytest.raises(StorageSafetyError) as caught:
        store.write("note", "笔记")
    assert caught.value.required_bytes == 6
    assert (tmp_path / "note").read_text() == "old content stays intact"
    disk_space.free = 50_006
    store.storage_safety.resume()
    store.write("note", "笔记")
    assert LocalFileStore(str(tmp_path)).read("note") == "笔记"


def test_reserve_and_failed_write_notification(tmp_path, disk_space, monkeypatch):
    failures: list[StorageSafetyError] = []
    controller = StorageSafetyController(
        [tmp_path], StorageSafetyConfig(), failures.append
    )
    store = LocalFileStore(str(tmp_path))
    store.storage_safety = controller
    disk_space.free = 10_000
    with pytest.raises(StorageSafetyError):
        controller.check()
    with controller.use_shutdown_reserve():
        store.write("result", "complete result")
    assert store.read("result") == "complete result"

    def fail_write(path: Path, contents: str) -> None:
        raise OSError(errno.ENOSPC, "no space left")

    monkeypatch.setattr("openhands.sdk.io.local.atomic_write_text", fail_write)
    with controller.use_shutdown_reserve(), pytest.raises(StorageSafetyError) as caught:
        store.write("uncommitted", "new result")
    assert caught.value.code == "StorageWriteFailed"
    assert caught.value.errno == errno.ENOSPC
    assert [error.code for error in failures] == [
        "StorageLowSpace",
        "StorageWriteFailed",
    ]
    assert not (tmp_path / "uncommitted").exists()
    assert store.read("result") == "complete result"


def test_atomic_replace_failure_keeps_old_file_and_removes_temporary_file(
    tmp_path, disk_space, monkeypatch
):
    store = LocalFileStore(str(tmp_path))
    store.write("note", "previous committed content")
    store.storage_safety = StorageSafetyController([tmp_path], StorageSafetyConfig())
    temporary_files: list[Path] = []

    def fail_replace(source: Path, destination: Path) -> None:
        assert source.read_text() == "new uncommitted content"
        temporary_files.append(source)
        raise OSError(errno.ENOSPC, "disk full during replacement")

    monkeypatch.setattr("openhands.sdk.utils.files.os.replace", fail_replace)
    with pytest.raises(StorageSafetyError) as caught:
        store.write("note", "new uncommitted content")
    assert caught.value.code == "StorageWriteFailed"
    assert caught.value.errno == errno.ENOSPC
    assert len(temporary_files) == 1
    assert not temporary_files[0].exists()
    assert store.read("note") == "previous committed content"
    assert LocalFileStore(str(tmp_path)).read("note") == "previous committed content"
    with pytest.raises(StorageSafetyError):
        store.write("note", "must not retry automatically")
    assert len(temporary_files) == 1
