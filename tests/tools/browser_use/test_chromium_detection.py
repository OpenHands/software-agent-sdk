"""Tests for Chromium detection and installation functionality."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openhands.tools.browser_use.impl import BrowserToolExecutor, _install_chromium


class TestChromiumDetection:
    """Test Chromium detection functionality."""

    def testcheck_chromium_available_system_binary(self):
        """Test detection of system-installed Chromium binary."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        with patch("shutil.which", return_value="/usr/bin/chromium"):
            result = executor.check_chromium_available()
            assert result == "/usr/bin/chromium"

    def testcheck_chromium_available_multiple_binaries(self):
        """Test that first available binary is returned."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)

        def mock_which(binary):
            if binary == "chromium":
                return "/usr/bin/chromium"
            return None

        with patch("shutil.which", side_effect=mock_which):
            result = executor.check_chromium_available()
            assert result == "/usr/bin/chromium"

    def testcheck_chromium_available_chrome_binary(self):
        """Test detection of Chrome binary when Chromium not available."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)

        def mock_which(binary):
            if binary == "google-chrome":
                return "/usr/bin/google-chrome"
            return None

        with patch("shutil.which", side_effect=mock_which):
            result = executor.check_chromium_available()
            assert result == "/usr/bin/google-chrome"

    def testcheck_chromium_available_standard_linux_path(self):
        """Test detection via standard Linux installation paths."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        chrome_path = Path("/usr/bin/google-chrome")

        def mock_exists(self):
            return str(self) == str(chrome_path)

        with (
            patch("shutil.which", return_value=None),
            patch.object(Path, "exists", mock_exists),
        ):
            result = executor.check_chromium_available()
            assert result == str(chrome_path)

    def testcheck_chromium_available_standard_macos_path(self):
        """Test detection via standard macOS installation paths."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        chrome_path = Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )

        def mock_exists(self):
            return str(self) == str(chrome_path)

        with (
            patch("shutil.which", return_value=None),
            patch.object(Path, "exists", mock_exists),
        ):
            result = executor.check_chromium_available()
            assert result == str(chrome_path)

    def testcheck_chromium_available_playwright_linux(self):
        """Test detection of Playwright-installed Chromium on Linux."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        mock_cache_dir = Path("/home/user/.cache/ms-playwright")
        mock_chromium_dir = mock_cache_dir / "chromium-1234"
        mock_chrome_path = mock_chromium_dir / "chrome-linux" / "chrome"

        def mock_exists(self):
            return str(self) in [str(mock_cache_dir), str(mock_chrome_path)]

        with (
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.home", return_value=Path("/home/user")),
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "glob") as mock_glob,
        ):
            mock_glob.return_value = [mock_chromium_dir]

            result = executor.check_chromium_available()
            assert result == str(mock_chrome_path)

    def testcheck_chromium_available_playwright_macos(self):
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
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.home", return_value=Path("/Users/user")),
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "glob") as mock_glob,
        ):
            mock_glob.return_value = [mock_chromium_dir]

            result = executor.check_chromium_available()
            assert result == str(mock_chrome_path)

    def testcheck_chromium_available_playwright_windows(self):
        """Test detection of Playwright-installed Chromium on Windows."""
        from openhands.tools.browser_use.impl_windows import WindowsBrowserToolExecutor

        executor = WindowsBrowserToolExecutor.__new__(WindowsBrowserToolExecutor)
        mock_cache_dir = Path("C:/Users/user/AppData/Local/ms-playwright")
        mock_chromium_dir = mock_cache_dir / "chromium-1234"
        mock_chrome_path = mock_chromium_dir / "chrome-win" / "chrome.exe"

        def mock_exists(self):
            return str(self) in [str(mock_cache_dir), str(mock_chrome_path)]

        def mock_environ_get(key, default=None):
            """Mock environment variable getter for Windows-specific tests."""
            if key == "LOCALAPPDATA":
                return "C:/Users/user/AppData/Local"
            return default

        with (
            patch("shutil.which", return_value=None),
            patch("os.environ.get", side_effect=mock_environ_get),
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "glob") as mock_glob,
        ):
            mock_glob.return_value = [mock_chromium_dir]

            result = executor.check_chromium_available()
            assert result == str(mock_chrome_path)

    def testcheck_chromium_available_not_found(self):
        """Test when no Chromium binary is found."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        with (
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.home", return_value=Path("/home/user")),
            patch.object(Path, "exists", return_value=False),
        ):
            result = executor.check_chromium_available()
            assert result is None

    def testcheck_chromium_available_playwright_cache_not_found(self):
        """Test when Playwright cache directory doesn't exist."""
        executor = BrowserToolExecutor.__new__(BrowserToolExecutor)
        with (
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
