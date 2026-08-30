"""Focused regression tests for Windows Chromium discovery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openhands.tools.browser_use import impl
from openhands.tools.browser_use.impl import (
    BrowserToolExecutor,
    _playwright_build_sort_key,
)


def _create_pe_executable(path: Path) -> Path:
    """Create a small valid PE-shaped fixture without a browser binary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = bytearray(0x44)
    contents[:2] = b"MZ"
    contents[0x3C:0x40] = (0x40).to_bytes(4, "little")
    contents[0x40:0x44] = b"PE\x00\x00"
    path.write_bytes(contents)
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def clear_detection_cache():
    BrowserToolExecutor.check_chromium_available.cache_clear()
    yield
    BrowserToolExecutor.check_chromium_available.cache_clear()


@pytest.fixture
def windows_browser_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    program_files = tmp_path / "Program Files"
    playwright_cache = tmp_path / "playwright cache with spaces \u7f13\u5b58"

    monkeypatch.setattr(impl.sys, "platform", "win32")
    monkeypatch.setattr(impl.shutil, "which", lambda _binary: None)
    monkeypatch.setenv("PROGRAMFILES", str(program_files))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "Program Files (x86)"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(playwright_cache))

    return {"program_files": program_files, "playwright_cache": playwright_cache}


def _system_chrome(paths: dict[str, Path]) -> Path:
    return _create_pe_executable(
        paths["program_files"] / "Google" / "Chrome" / "Application" / "chrome.exe"
    )


def test_windows_prefers_playwright_chromium_over_system_chrome(
    windows_browser_paths: dict[str, Path],
):
    system_chrome = _system_chrome(windows_browser_paths)
    managed_chromium = _create_pe_executable(
        windows_browser_paths["playwright_cache"]
        / "chromium-1200"
        / "chrome-win64"
        / "chrome.exe"
    )

    assert system_chrome.is_file()
    assert BrowserToolExecutor.check_chromium_available() == str(managed_chromium)


def test_windows_chooses_latest_build_deterministically(
    windows_browser_paths: dict[str, Path],
):
    cache = windows_browser_paths["playwright_cache"]
    older = _create_pe_executable(
        cache / "chromium-999" / "chrome-win64" / "chrome.exe"
    )
    latest = _create_pe_executable(
        cache / "chromium-1200" / "chrome-win64" / "chrome.exe"
    )

    assert older.is_file()
    assert BrowserToolExecutor.check_chromium_available() == str(latest)


def test_windows_chooses_latest_build_across_cache_roots(
    windows_browser_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    cache = windows_browser_paths["playwright_cache"]
    older_cache = cache / "old package"
    newer_cache = cache / "new package"
    _create_pe_executable(older_cache / "chromium-1200" / "chrome-win64" / "chrome.exe")
    latest = _create_pe_executable(
        newer_cache / "chromium-1300" / "chrome-win64" / "chrome.exe"
    )
    monkeypatch.setattr(
        impl,
        "_playwright_cache_dirs",
        lambda _platform: [older_cache, newer_cache],
    )

    assert BrowserToolExecutor.check_chromium_available() == str(latest)


def test_windows_skips_missing_executable_and_uses_older_build(
    windows_browser_paths: dict[str, Path],
):
    cache = windows_browser_paths["playwright_cache"]
    (cache / "chromium-1300" / "chrome-win64").mkdir(parents=True)
    older = _create_pe_executable(
        cache / "chromium-1200" / "chrome-win64" / "chrome.exe"
    )

    assert BrowserToolExecutor.check_chromium_available() == str(older)


def test_windows_ranks_malformed_revision_below_numeric_build(
    windows_browser_paths: dict[str, Path],
):
    cache = windows_browser_paths["playwright_cache"]
    _create_pe_executable(cache / "chromium-\u00b2" / "chrome-win64" / "chrome.exe")
    numeric = _create_pe_executable(
        cache / "chromium-1200" / "chrome-win64" / "chrome.exe"
    )

    assert BrowserToolExecutor.check_chromium_available() == str(numeric)


def test_playwright_sort_key_handles_extreme_revision_without_raising():
    revision = "9" * 5000

    assert _playwright_build_sort_key(Path(f"chromium-{revision}"))[0] == -1


def test_playwright_sort_key_rejects_negative_revision():
    assert _playwright_build_sort_key(Path("chromium--1"))[0] == -1


def test_windows_skips_missing_or_invalid_managed_build(
    windows_browser_paths: dict[str, Path],
):
    cache = windows_browser_paths["playwright_cache"]
    invalid = cache / "chromium-1300" / "chrome-win64" / "chrome.exe"
    invalid.parent.mkdir(parents=True)
    invalid.write_bytes(b"not a PE executable")
    fallback = _system_chrome(windows_browser_paths)

    assert BrowserToolExecutor.check_chromium_available() == str(fallback)


def test_windows_falls_back_when_playwright_cache_is_missing(
    windows_browser_paths: dict[str, Path],
):
    fallback = _system_chrome(windows_browser_paths)

    assert not windows_browser_paths["playwright_cache"].exists()
    assert BrowserToolExecutor.check_chromium_available() == str(fallback)


def test_windows_falls_back_when_cache_glob_has_runtime_error(
    windows_browser_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    fallback = _system_chrome(windows_browser_paths)

    original_glob = Path.glob

    def broken_glob(path: Path, pattern: str):
        if path == windows_browser_paths["playwright_cache"]:
            raise RuntimeError("cache changed during enumeration")
        return original_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", broken_glob)

    assert BrowserToolExecutor.check_chromium_available() == str(fallback)


def test_windows_falls_back_when_cache_glob_has_value_error(
    windows_browser_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    fallback = _system_chrome(windows_browser_paths)

    def broken_glob(_path: Path, _pattern: str):
        raise ValueError("invalid cache path")

    monkeypatch.setattr(Path, "glob", broken_glob)

    assert BrowserToolExecutor.check_chromium_available() == str(fallback)


def test_windows_discovers_relative_unicode_playwright_cache(
    windows_browser_paths: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project = tmp_path / "project with spaces"
    relative_cache = "playwright-\u6d4b\u8bd5 cache"
    managed = _create_pe_executable(
        project / relative_cache / "chromium-1200" / "chrome-win64" / "chrome.exe"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", relative_cache)
    monkeypatch.setenv("INIT_CWD", str(project))

    assert BrowserToolExecutor.check_chromium_available() == str(managed)


def test_windows_discovers_hermetic_playwright_cache(
    windows_browser_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    package_root = windows_browser_paths["playwright_cache"].parent / "playwright"
    managed = _create_pe_executable(
        package_root
        / "driver"
        / "package"
        / ".local-browsers"
        / "chromium-1200"
        / "chrome-win64"
        / "chrome.exe"
    )
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    monkeypatch.setattr(
        impl.importlib.util,
        "find_spec",
        lambda name: (
            SimpleNamespace(submodule_search_locations=[str(package_root)])
            if name == "playwright"
            else None
        ),
    )

    assert BrowserToolExecutor.check_chromium_available() == str(managed)


def test_windows_supports_special_revision_layout(
    windows_browser_paths: dict[str, Path],
):
    cache = windows_browser_paths["playwright_cache"]
    _create_pe_executable(
        cache
        / "chromium_headless_shell_win64_special-1400"
        / "chrome-win64"
        / "chrome.exe"
    )
    managed = _create_pe_executable(
        cache / "chromium_win64_special-1300" / "chrome-win64" / "chrome.exe"
    )

    assert BrowserToolExecutor.check_chromium_available() == str(managed)


def test_windows_system_path_fallback_skips_invalid_executable(
    windows_browser_paths: dict[str, Path],
):
    invalid = (
        windows_browser_paths["program_files"]
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe"
    )
    invalid.parent.mkdir(parents=True)
    invalid.write_bytes(b"not a PE executable")
    edge = _create_pe_executable(
        windows_browser_paths["program_files"]
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe"
    )

    assert BrowserToolExecutor.check_chromium_available() == str(edge)


def test_windows_path_binary_fallback_validates_pe_header(
    windows_browser_paths: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path_binary = tmp_path / "bin" / "chrome.exe"
    _create_pe_executable(path_binary)
    monkeypatch.setattr(
        impl.shutil,
        "which",
        lambda binary: str(path_binary) if binary == "chrome" else None,
    )

    assert BrowserToolExecutor.check_chromium_available() == str(path_binary)


def test_windows_ignores_unreadable_hermetic_cache(
    windows_browser_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    fallback = _system_chrome(windows_browser_paths)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    monkeypatch.setattr(impl.importlib.util, "find_spec", lambda _name: None)

    assert BrowserToolExecutor.check_chromium_available() == str(fallback)


def test_windows_falls_back_when_hermetic_module_probe_fails(
    windows_browser_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    fallback = _system_chrome(windows_browser_paths)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")

    def broken_find_spec(_name: str):
        raise RuntimeError("import hook changed during discovery")

    monkeypatch.setattr(impl.importlib.util, "find_spec", broken_find_spec)

    assert BrowserToolExecutor.check_chromium_available() == str(fallback)


def test_non_windows_cache_glob_runtime_error_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        impl, "_playwright_cache_dirs", lambda _platform: [Path("cache")]
    )
    monkeypatch.setattr(Path, "exists", lambda _path: True)
    monkeypatch.setattr(
        Path,
        "glob",
        lambda _path, _pattern: (_ for _ in ()).throw(RuntimeError("loop")),
    )

    with pytest.raises(RuntimeError, match="loop"):
        impl._playwright_chromium_install_paths("linux")


def test_windows_skips_candidate_when_metadata_is_unreadable(
    windows_browser_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    cache = windows_browser_paths["playwright_cache"]
    newest = _create_pe_executable(
        cache / "chromium-1300" / "chrome-win64" / "chrome.exe"
    )
    older = _create_pe_executable(
        cache / "chromium-1200" / "chrome-win64" / "chrome.exe"
    )
    original_exists = Path.exists

    def unreadable(path: Path):
        if path == newest:
            raise PermissionError("metadata unavailable")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", unreadable)

    assert BrowserToolExecutor.check_chromium_available() == str(older)
