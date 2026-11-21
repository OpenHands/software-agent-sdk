import logging
import os
import stat
import sys
from pathlib import Path

import pytest

from openhands.sdk.utils import files
from openhands.sdk.utils.files import (
    atomic_write_text,
    is_temp_file,
    read_text_retrying,
)


@pytest.mark.parametrize("mode", [0o600, None], ids=["mkstemp", "umask"])
def test_atomic_write_text_temp_file_is_marked_as_a_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int | None
) -> None:
    temp_paths: list[Path] = []
    real_replace = os.replace

    def recording_replace(src, dst):
        temp_paths.append(Path(src))
        real_replace(src, dst)

    monkeypatch.setattr(files.os, "replace", recording_replace)

    names = ("base_state.json", "event-00000-abc.json", ".eventlog-len-3.marker")
    for name in names:
        atomic_write_text(tmp_path / name, "{}", mode=mode)

    assert [path.parent for path in temp_paths] == [tmp_path] * len(names)
    for name, temp_path in zip(names, temp_paths, strict=True):
        assert temp_path.name.startswith(f".{name}.")
        assert temp_path.name.endswith(".tmp")
        assert is_temp_file(temp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(names)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.parametrize(
    ("umask", "mode", "expected"),
    [
        pytest.param(0o022, 0o600, 0o600, id="default-owner-only"),
        pytest.param(0o022, 0o640, 0o640, id="explicit"),
        pytest.param(0o022, None, 0o644, id="umask-022"),
        pytest.param(0o077, None, 0o600, id="umask-077"),
    ],
)
def test_atomic_write_text_mode(
    tmp_path: Path, umask: int, mode: int | None, expected: int
) -> None:
    path = tmp_path / "data.json"
    path.write_text("old")
    path.chmod(0o604)
    previous_umask = os.umask(umask)
    try:
        atomic_write_text(path, "new", mode=mode)
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(path.stat().st_mode) == expected
    assert path.read_text() == "new"


@pytest.mark.skipif(
    sys.platform == "win32" or getattr(os, "geteuid", lambda: -1)() == 0,
    reason="needs a user who may not give files to root",
)
def test_atomic_write_text_owner_is_best_effort(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "data.json"

    with caplog.at_level(logging.WARNING):
        atomic_write_text(path, "new", owner=(0, 0))

    assert path.read_text() == "new"
    assert path.stat().st_uid == tmp_path.stat().st_uid != 0
    assert any("Could not keep the owner" in r.message for r in caplog.records)


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


def _replace_failing(times: int, error: OSError, calls: list[Path]):
    """An ``os.replace`` that raises ``error`` on its first ``times`` calls."""
    real_replace = os.replace

    def replace(src, dst):
        calls.append(Path(dst))
        if len(calls) <= times:
            raise error
        real_replace(src, dst)

    return replace


@pytest.mark.parametrize("failures", [1, len(files._PERMISSION_ERROR_RETRY_DELAYS)])
def test_atomic_write_text_retries_replace_blocked_by_an_open_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failures: int
) -> None:
    """On Windows, a replace that meets a reader's open handle is retried."""
    path = tmp_path / "data.json"
    path.write_text("old")
    calls: list[Path] = []
    sleeps: list[float] = []
    monkeypatch.setattr(files, "_RETRY_ON_PERMISSION_ERROR", True)
    monkeypatch.setattr(files.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        files.os, "replace", _replace_failing(failures, PermissionError(13, "x"), calls)
    )

    atomic_write_text(path, "new")

    assert path.read_text() == "new"
    assert len(calls) == failures + 1
    assert sleeps == list(files._PERMISSION_ERROR_RETRY_DELAYS[:failures])
    assert [p.name for p in tmp_path.iterdir()] == ["data.json"]


def test_atomic_write_text_raises_when_replace_stays_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry is bounded: past the last delay the error is raised, the
    destination is unchanged, and the temp file is removed.
    """
    path = tmp_path / "data.json"
    path.write_text("old")
    calls: list[Path] = []
    sleeps: list[float] = []
    monkeypatch.setattr(files, "_RETRY_ON_PERMISSION_ERROR", True)
    monkeypatch.setattr(files.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        files.os,
        "replace",
        _replace_failing(
            len(files._PERMISSION_ERROR_RETRY_DELAYS) + 1,
            PermissionError(13, "x"),
            calls,
        ),
    )

    with pytest.raises(PermissionError):
        atomic_write_text(path, "new")

    assert len(calls) == len(files._PERMISSION_ERROR_RETRY_DELAYS) + 1
    assert sleeps == list(files._PERMISSION_ERROR_RETRY_DELAYS)
    assert path.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["data.json"]


@pytest.mark.parametrize(
    ("retry_enabled", "error"),
    [
        (False, PermissionError(13, "x")),
        (True, OSError(28, "No space left on device")),
    ],
    ids=["permission_error_off_windows", "other_os_error_on_windows"],
)
def test_atomic_write_text_does_not_retry_other_replace_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retry_enabled: bool,
    error: OSError,
) -> None:
    """Only a ``PermissionError`` on Windows is retried; anything else, which
    a retry cannot fix, is raised at once.
    """
    path = tmp_path / "data.json"
    calls: list[Path] = []
    sleeps: list[float] = []
    monkeypatch.setattr(files, "_RETRY_ON_PERMISSION_ERROR", retry_enabled)
    monkeypatch.setattr(files.time, "sleep", sleeps.append)
    monkeypatch.setattr(files.os, "replace", _replace_failing(1, error, calls))

    with pytest.raises(type(error)):
        atomic_write_text(path, "new")

    assert len(calls) == 1
    assert sleeps == []
    assert list(tmp_path.iterdir()) == []


_MAX_RETRIES = len(files._PERMISSION_ERROR_RETRY_DELAYS)


@pytest.mark.parametrize(
    ("retry_enabled", "failures", "reads", "succeeds"),
    [
        (True, 1, 2, True),
        (True, _MAX_RETRIES, _MAX_RETRIES + 1, True),
        (True, _MAX_RETRIES + 1, _MAX_RETRIES + 1, False),
        (False, 1, 1, False),
    ],
    ids=["one_failure", "max_failures", "too_many_failures", "off_windows"],
)
def test_read_text_retrying_retries_a_read_blocked_by_a_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retry_enabled: bool,
    failures: int,
    reads: int,
    succeeds: bool,
) -> None:
    """On Windows, a read that meets a concurrent replace fails with
    ``PermissionError`` and is retried, up to the last delay.
    """
    path = tmp_path / "data.json"
    path.write_text("content", encoding="utf-8")
    calls: list[Path] = []
    real_read_text = Path.read_text

    def read_text(self: Path, *args, **kwargs) -> str:
        calls.append(self)
        if len(calls) <= failures:
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(files, "_RETRY_ON_PERMISSION_ERROR", retry_enabled)
    monkeypatch.setattr(files.time, "sleep", lambda delay: None)
    monkeypatch.setattr(Path, "read_text", read_text)

    if succeeds:
        assert read_text_retrying(path) == "content"
    else:
        with pytest.raises(PermissionError):
            read_text_retrying(path)

    assert calls == [path] * reads
