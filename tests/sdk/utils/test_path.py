import os
from pathlib import Path

import pytest

from openhands.sdk.utils.path import (
    get_user_persistence_dir,
    is_absolute_path_source,
    is_host_absolute_path,
    is_local_path_source,
    posix_path_name,
    to_posix_path,
)


def test_get_user_persistence_dir_defaults_to_home_openhands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OH_PERSISTENCE_DIR", raising=False)
    fake_home = Path("/fake/home")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert get_user_persistence_dir() == fake_home / ".openhands"


def test_get_user_persistence_dir_honors_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path))
    assert get_user_persistence_dir() == tmp_path


def test_get_user_persistence_dir_resolved_at_call_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OH_PERSISTENCE_DIR", raising=False)
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert get_user_persistence_dir() == fake_home / ".openhands"

    override = tmp_path / "persist"
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(override))
    assert get_user_persistence_dir() == override


def test_to_posix_path_normalizes_backslashes_without_resolving():
    assert to_posix_path(r"C:\work\repo\file.py") == "C:/work/repo/file.py"


def test_to_posix_path_accepts_path_objects():
    assert to_posix_path(Path("nested") / "file.py") == "nested/file.py"


def test_posix_path_name_handles_windows_separators():
    assert posix_path_name(r"C:\work\repo\file.py") == "file.py"


def test_is_local_path_source_detects_windows_absolute_paths():
    assert is_local_path_source(r"C:\work\repo")


def test_is_local_path_source_keeps_url_sources_remote():
    assert not is_local_path_source("https://github.com/org/repo")


def test_is_local_path_source_detects_backslash_path_syntax():
    assert is_local_path_source(r"relative\plugin")
    assert is_local_path_source(r"\rooted")


def test_is_local_path_source_detects_dot_paths():
    assert is_local_path_source(".")
    assert is_local_path_source("..")
    assert is_local_path_source(".openhands")


def test_is_absolute_path_source_detects_posix_and_windows_paths():
    assert is_absolute_path_source("/workspace/file.py")
    assert is_absolute_path_source(r"\workspace\file.py")
    assert is_absolute_path_source(r"C:\workspace\file.py")
    assert not is_absolute_path_source("relative/file.py")
    assert not is_absolute_path_source(r"relative\file.py")


def test_is_host_absolute_path_uses_current_platform_semantics():
    assert is_host_absolute_path("/workspace/file.py")
    assert not is_host_absolute_path("relative/file.py")
    assert is_host_absolute_path(Path("/workspace") / "file.py")

    if os.name == "nt":
        assert is_host_absolute_path(r"C:\workspace\file.py")
    else:
        assert not is_host_absolute_path(r"C:\workspace\file.py")
