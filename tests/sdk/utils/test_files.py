import os
import stat
from pathlib import Path

import pytest

from openhands.sdk.utils import files
from openhands.sdk.utils.files import atomic_write_text, is_temp_file


@pytest.mark.skipif(os.name != "posix", reason="Requires directory fsync")
def test_atomic_write_text_syncs_directory_after_replacing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "event-00000-abc.json"
    path.write_text("old")
    synced: list[str] = []
    real_fsync = os.fsync

    def record_fsync(fd: int) -> None:
        descriptor = os.fstat(fd)
        if stat.S_ISDIR(descriptor.st_mode):
            assert os.path.samestat(descriptor, tmp_path.stat())
            assert path.read_text() == "new"
            synced.append("directory")
        else:
            assert path.read_text() == "old"
            synced.append("file")
        real_fsync(fd)

    monkeypatch.setattr(files.os, "fsync", record_fsync)

    atomic_write_text(path, "new")

    assert synced == ["file", "directory"]


def test_atomic_write_text_temp_file_is_marked_as_a_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_paths: list[Path] = []
    real_replace = os.replace

    def recording_replace(src, dst):
        temp_paths.append(Path(src))
        real_replace(src, dst)

    monkeypatch.setattr(files.os, "replace", recording_replace)

    names = ("base_state.json", "event-00000-abc.json", ".eventlog-len-3.marker")
    for name in names:
        atomic_write_text(tmp_path / name, "{}")

    assert [path.parent for path in temp_paths] == [tmp_path] * len(names)
    for name, temp_path in zip(names, temp_paths, strict=True):
        assert temp_path.name.startswith(f".{name}.")
        assert temp_path.name.endswith(".tmp")
        assert is_temp_file(temp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(names)


@pytest.mark.parametrize(
    "name",
    [
        ".base_state.json.4867ztqe.tmp",
        "..eventlog-len-3.marker.k2_9xq0z.tmp",
        "owner_lease.tmp",
    ],
)
def test_is_temp_file_matches_tmp_names(name: str) -> None:
    assert is_temp_file(Path(name))


@pytest.mark.parametrize(
    "name",
    [
        "base_state.json",
        "meta.json",
        "event-00000-0123abcd-4567-89ef.json",
        "owner_lease.json",
        ".owner_lease.lock",
        ".eventlog.lock",
        ".eventlog-len-12.marker",
        ".hidden",
        # Ordinary dotfiles, which a pattern for mkstemp's random part matched.
        ".env.template",
        ".cache.metadata",
    ],
)
def test_is_temp_file_ignores_other_names(name: str) -> None:
    assert not is_temp_file(Path(name))
