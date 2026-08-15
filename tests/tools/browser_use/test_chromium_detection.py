"""Tests for Chromium detection and installation functionality."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openhands.tools.browser_use.impl import BrowserToolExecutor, _install_chromium


@pytest.fixture(autouse=True)
def clear_chromium_detection_cache():
    BrowserToolExecutor.check_chromium_available.cache_clear()
    yield
    BrowserToolExecutor.check_chromium_available.cache_clear()


def _create_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(0o755)
    return path


@pytest.fixture
def windows_browser_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    program_files = tmp_path / "Program Files"
    local_app_data = tmp_path / "AppData" / "Local"
    playwright_cache = tmp_path / "playwright-browsers"

    monkeypatch.setattr("openhands.tools.browser_use.impl.sys.platform", "win32")
    monkeypatch.setattr(
        "openhands.tools.browser_use.impl.shutil.which", lambda _binary: None
    )
    monkeypatch.setenv("PROGRAMFILES", str(program_files))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "Program Files (x86)"))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(playwright_cache))

    return {
        "program_files": program_files,
        "playwright_cache": playwright_cache,
    }


def test_windows_prefers_playwright_chromium_over_system_chrome(
    windows_browser_paths: dict[str, Path],
):
    system_chrome = _create_executable(
        windows_browser_paths["program_files"]
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe"
    )
    playwright_chromium = _create_executable(
        windows_browser_paths["playwright_cache"]
        / "chromium-1200"
        / "chrome-win64"
        / "chrome.exe"
    )

    result = BrowserToolExecutor.check_chromium_available()

    assert system_chrome.is_file()
    assert result == str(playwright_chromium)


def test_windows_prefers_latest_available_playwright_build(
    windows_browser_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    playwright_cache = windows_browser_paths["playwright_cache"]
    older_chromium = _create_executable(
        playwright_cache / "chromium-999" / "chrome-win64" / "chrome.exe"
    )
    latest_chromium = _create_executable(
        playwright_cache / "chromium-1200" / "chrome-win64" / "chrome.exe"
    )
    original_glob = Path.glob

    def reverse_build_order(path: Path, pattern: str):
        if path == playwright_cache and pattern == "chromium-*":
            return iter([older_chromium.parents[1], latest_chromium.parents[1]])
        return original_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", reverse_build_order)

    result = BrowserToolExecutor.check_chromium_available()

    assert result == str(latest_chromium)


def test_windows_skips_playwright_build_without_executable(
    windows_browser_paths: dict[str, Path],
):
    missing_executable = (
        windows_browser_paths["playwright_cache"]
        / "chromium-1300"
        / "chrome-win64"
        / "chrome.exe"
    )
    missing_executable.parent.mkdir(parents=True)
    system_chrome = _create_executable(
        windows_browser_paths["program_files"]
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe"
    )

    result = BrowserToolExecutor.check_chromium_available()

    assert not missing_executable.exists()
    assert result == str(system_chrome)


def test_windows_falls_back_to_system_browser_without_playwright(
    windows_browser_paths: dict[str, Path],
):
    system_edge = _create_executable(
        windows_browser_paths["program_files"]
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe"
    )

    result = BrowserToolExecutor.check_chromium_available()

    assert not windows_browser_paths["playwright_cache"].exists()
    assert result == str(system_edge)


class TestChromiumDetection:
    """Test Chromium detection functionality."""

    def test_check_chromium_available_system_binary(self):
        """Test detection of system-installed Chromium binary."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        with (
            patch.object(Path, "exists", return_value=False),
            patch("shutil.which", return_value="/usr/bin/chromium"),
        ):
            result = executor.check_chromium_available()
            assert result == "/usr/bin/chromium"

    def test_check_chromium_available_is_cached(self):
        """Test that Chromium detection is memoized across repeated calls."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        with (
            patch.object(Path, "exists", return_value=False),
            patch("shutil.which", return_value="/usr/bin/chromium") as mock_which,
        ):
            assert executor.check_chromium_available() == "/usr/bin/chromium"
            assert executor.check_chromium_available() == "/usr/bin/chromium"

        assert mock_which.call_count == 1

    def test_check_chromium_available_multiple_binaries(self):
        """Test that first available binary is returned."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)

        def mock_which(binary):
            if binary == "chromium":
                return "/usr/bin/chromium"
            return None

        with (
            patch("openhands.tools.browser_use.impl.sys.platform", "linux"),
            patch.object(Path, "exists", return_value=False),
            patch("shutil.which", side_effect=mock_which),
        ):
            result = executor.check_chromium_available()
            assert result == "/usr/bin/chromium"

    def test_check_chromium_available_chrome_binary(self):
        """Test detection of Chrome binary when Chromium not available."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)

        def mock_which(binary):
            if binary == "google-chrome":
                return "/usr/bin/google-chrome"
            return None

        with (
            patch("openhands.tools.browser_use.impl.sys.platform", "linux"),
            patch.object(Path, "exists", return_value=False),
            patch("shutil.which", side_effect=mock_which),
        ):
            result = executor.check_chromium_available()
            assert result == "/usr/bin/google-chrome"

    def test_check_chromium_available_standard_linux_path(self):
        """Test detection via standard Linux installation paths."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        chrome_path = Path("/usr/bin/google-chrome")

        def mock_exists(self):
            return str(self) == str(chrome_path)

        with (
            patch("openhands.tools.browser_use.impl.sys.platform", "linux"),
            patch("shutil.which", return_value=None),
            patch.object(Path, "exists", mock_exists),
        ):
            result = executor.check_chromium_available()
            assert result == str(chrome_path)

    def test_check_chromium_available_standard_macos_path(self):
        """Test detection via standard macOS installation paths."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        chrome_path = Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )

        def mock_exists(self):
            return str(self) == str(chrome_path)

        with (
            patch("openhands.tools.browser_use.impl.sys.platform", "darwin"),
            patch("shutil.which", return_value=None),
            patch.object(Path, "exists", mock_exists),
        ):
            result = executor.check_chromium_available()
            assert result == str(chrome_path)

    def test_check_chromium_available_standard_windows_edge_path(self):
        """Test detection via standard Windows Edge installation path."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        edge_path = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")

        def mock_exists(self):
            return str(self) == str(edge_path)

        def mock_environ_get(key, default=None):
            if key == "PROGRAMFILES":
                return "C:/Program Files"
            if key == "PROGRAMFILES(X86)":
                return "C:/Program Files (x86)"
            if key == "LOCALAPPDATA":
                return "C:/Users/user/AppData/Local"
            return default

        with (
            patch("openhands.tools.browser_use.impl.sys.platform", "win32"),
            patch("shutil.which", return_value=None),
            patch("os.environ.get", side_effect=mock_environ_get),
            patch.object(Path, "exists", mock_exists),
            patch(
                "openhands.tools.browser_use.impl._is_browser_executable",
                side_effect=lambda path, _platform=None: str(path) == str(edge_path),
            ),
        ):
            result = executor.check_chromium_available()
            assert result == str(edge_path)

    def test_check_chromium_available_playwright_linux(self):
        """Test detection of Playwright-installed Chromium on Linux."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        mock_cache_dir = Path("/home/user/.cache/ms-playwright")
        mock_chromium_dir = mock_cache_dir / "chromium-1234"
        mock_chrome_path = mock_chromium_dir / "chrome-linux" / "chrome"

        def mock_exists(self):
            return str(self) in [str(mock_cache_dir), str(mock_chrome_path)]

        with (
            patch("openhands.tools.browser_use.impl.sys.platform", "linux"),
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.home", return_value=Path("/home/user")),
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "glob") as mock_glob,
        ):
            mock_glob.return_value = [mock_chromium_dir]

            result = executor.check_chromium_available()
            assert result == str(mock_chrome_path)

    def test_check_chromium_available_playwright_macos(self):
        """Test detection of Playwright-installed Chromium on macOS."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        mock_cache_dir = Path("/Users/user/Library/Caches/ms-playwright")
        mock_chromium_dir = mock_cache_dir / "chromium-1234"
        mock_chrome_path = (
            mock_chromium_dir
            / "chrome-mac"
            / "Chromium.app"
            / "Contents"
            / "MacOS"
            / "Chromium"
        )

        def mock_exists(self):
            return str(self) in [str(mock_cache_dir), str(mock_chrome_path)]

        with (
            patch("openhands.tools.browser_use.impl.sys.platform", "darwin"),
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.home", return_value=Path("/Users/user")),
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "glob") as mock_glob,
        ):
            mock_glob.return_value = [mock_chromium_dir]

            result = executor.check_chromium_available()
            assert result == str(mock_chrome_path)

    def test_check_chromium_available_playwright_windows(self):
        """Test detection of Playwright-installed Chromium on Windows."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        mock_cache_dir = Path("C:/Users/user/AppData/Local/ms-playwright")
        mock_chromium_dir = mock_cache_dir / "chromium-1234"
        mock_chrome_path = mock_chromium_dir / "chrome-win64" / "chrome.exe"

        def mock_exists(self):
            return str(self) in [str(mock_cache_dir), str(mock_chrome_path)]

        def mock_environ_get(key, default=None):
            """Mock environment variable getter for Windows-specific tests."""
            if key == "LOCALAPPDATA":
                return "C:/Users/user/AppData/Local"
            return default

        with (
            patch("openhands.tools.browser_use.impl.sys.platform", "win32"),
            patch("shutil.which", return_value=None),
            patch("os.environ.get", side_effect=mock_environ_get),
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "glob") as mock_glob,
            patch(
                "openhands.tools.browser_use.impl._is_browser_executable",
                side_effect=lambda path, _platform=None: str(path)
                == str(mock_chrome_path),
            ),
        ):
            mock_glob.return_value = [mock_chromium_dir]

            result = executor.check_chromium_available()
            assert result == str(mock_chrome_path)

    def test_check_chromium_available_not_found(self):
        """Test when no Chromium binary is found."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        with (
            patch("openhands.tools.browser_use.impl.sys.platform", "linux"),
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.home", return_value=Path("/home/user")),
            patch.object(Path, "exists", return_value=False),
        ):
            result = executor.check_chromium_available()
            assert result is None

    def test_check_chromium_available_playwright_cache_not_found(self):
        """Test when Playwright cache directory doesn't exist."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        with (
            patch("openhands.tools.browser_use.impl.sys.platform", "linux"),
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.home", return_value=Path("/home/user")),
            patch.object(Path, "exists", return_value=False),
        ):
            result = executor.check_chromium_available()
            assert result is None


class TestChromiumInstallation:
    """Test Chromium installation functionality."""

    def test_install_chromium_success(self):
        """Test successful Chromium installation."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("shutil.which", return_value="/usr/bin/uvx"),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = _install_chromium()
            assert result is True

    def test_install_chromium_uvx_not_found(self):
        """Test Chromium installation when uvx is not available."""
        with patch("shutil.which", return_value=None):
            result = _install_chromium()
            assert result is False

    def test_install_chromium_subprocess_failure(self):
        """Test Chromium installation when subprocess fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Installation failed"

        with (
            patch("shutil.which", return_value="/usr/bin/uvx"),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = _install_chromium()
            assert result is False

    def test_install_chromium_timeout(self):
        """Test Chromium installation timeout."""
        with (
            patch("shutil.which", return_value="/usr/bin/uvx"),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("uvx", 300)),
        ):
            result = _install_chromium()
            assert result is False

    def test_install_chromium_file_not_found(self):
        """Test Chromium installation when uvx command is not found."""
        with (
            patch("shutil.which", return_value="/usr/bin/uvx"),
            patch("subprocess.run", side_effect=FileNotFoundError("uvx not found")),
        ):
            result = _install_chromium()
            assert result is False

    def test_install_chromium_generic_exception(self):
        """Test Chromium installation with generic exception."""
        with (
            patch("shutil.which", return_value="/usr/bin/uvx"),
            patch("subprocess.run", side_effect=Exception("Generic error")),
        ):
            result = _install_chromium()
            assert result is False


class TestEnsureChromiumAvailable:
    """Test ensure Chromium available functionality."""

    def test_ensure_chromium_available_already_available(self):
        """Test when Chromium is already available."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        with patch.object(
            executor, "check_chromium_available", return_value="/usr/bin/chromium"
        ):
            result = executor._ensure_chromium_available()
            assert result == "/usr/bin/chromium"

    def test_ensure_chromium_available_not_found_raises_error(self):
        """Test that clear error is raised when Chromium is not available."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        with patch.object(executor, "check_chromium_available", return_value=None):
            with pytest.raises(Exception) as exc_info:
                executor._ensure_chromium_available()

            error_message = str(exc_info.value)
            assert "Chromium is required for browser operations" in error_message
            assert "uvx playwright install chromium" in error_message
            assert "pip install playwright" in error_message
            assert "sudo apt install chromium-browser" in error_message
            assert "brew install chromium" in error_message
            assert "winget install Chromium.Chromium" in error_message
            assert "restart your application" in error_message
