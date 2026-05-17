import os
from pathlib import Path

from openhands.sdk.utils.path import (
    is_absolute_path_source,
    is_host_absolute_path,
    is_local_path_source,
    posix_path_name,
    to_posix_path,
)


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
