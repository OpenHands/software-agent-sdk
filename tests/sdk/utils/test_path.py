import os
from pathlib import Path

import pytest

from openhands.sdk.utils.path import (
    get_user_persistence_dir,
    is_absolute_path_source,
    is_host_absolute_path,
    is_local_path_source,
    posix_path_name,
    resolves_within,
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


def test_get_user_persistence_dir_expands_tilde_in_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "home"
    # expanduser() reads HOME/USERPROFILE, not Path.home's patched classmethod.
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("OH_PERSISTENCE_DIR", "~/persist")
    assert get_user_persistence_dir() == fake_home / "persist"


def test_get_user_persistence_dir_anchors_relative_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relative value is pinned to the import-time CWD, not the current one."""
    import openhands.sdk.utils.path as path_module

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    monkeypatch.setattr(path_module, "_INITIAL_CWD", anchor)
    monkeypatch.setenv("OH_PERSISTENCE_DIR", "persist")
    assert get_user_persistence_dir() == anchor / "persist"

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert get_user_persistence_dir() == anchor / "persist"


def test_get_user_persistence_dir_default_overrides_home_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OH_PERSISTENCE_DIR", raising=False)
    fallback = tmp_path / "fallback"
    assert get_user_persistence_dir(fallback) == fallback

    override = tmp_path / "persist"
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(override))
    assert get_user_persistence_dir(fallback) == override


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


@pytest.mark.parametrize("value", ["", "/", "\\"])
def test_posix_path_name_of_empty_or_root_is_empty(value: str) -> None:
    assert posix_path_name(value) == ""


def test_posix_path_name_ignores_trailing_separator() -> None:
    assert posix_path_name("a/b/") == "b"
    assert posix_path_name("a\\b\\") == "b"


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_sources_are_neither_local_nor_absolute(value: str) -> None:
    assert not is_local_path_source(value)
    assert not is_absolute_path_source(value)
    assert not is_host_absolute_path(value)


@pytest.mark.parametrize(
    "value",
    ["git@github.com:org/repo.git", "github:org/repo", "org/repo", "C:relative"],
)
def test_is_local_path_source_rejects_remote_and_drive_relative(value: str) -> None:
    assert not is_local_path_source(value)


def test_resolves_within_accepts_inner_symlink(tmp_path: Path) -> None:
    (tmp_path / "real.txt").write_text("x")
    link = tmp_path / "link.txt"
    link.symlink_to(tmp_path / "real.txt")

    assert resolves_within(link, tmp_path)


def test_resolves_within_rejects_escaping_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    link = root / "link.txt"
    link.symlink_to(tmp_path / "secret.txt")

    assert not resolves_within(link, root)


def test_resolves_within_rejects_sibling_sharing_root_prefix(tmp_path: Path) -> None:
    root = tmp_path / "root"
    sibling = tmp_path / "root2"
    root.mkdir()
    sibling.mkdir()

    assert not resolves_within(sibling, root)
    assert not resolves_within(root / ".." / "root2", root)
