"""Windows-specific browser tool executor implementation."""

import os
import shutil
from pathlib import Path

from openhands.tools.browser_use.impl import BrowserToolExecutor


class WindowsBrowserToolExecutor(BrowserToolExecutor):
    """Windows-specific browser tool executor with Chromium detection.

    This class extends BrowserToolExecutor to provide Windows-specific
    browser detection logic for Chrome and Edge installations.
    """

    def check_chromium_available(self) -> str | None:
        """Check if a Chromium/Chrome binary is available on Windows.

        Checks:
        1. Standard PATH binaries
        2. Common Windows installation paths for Chrome and Edge
        3. Playwright cache directory using LOCALAPPDATA

        Returns:
            Path to Chromium binary if found, None otherwise
        """
        # First check if chromium/chrome is in PATH
        for binary in ("chromium", "chromium-browser", "google-chrome", "chrome"):
            if path := shutil.which(binary):
                return path

        # Check common Windows installation paths
        # Short-circuit on first found browser for efficiency
        env_vars = [
            ("PROGRAMFILES", "C:\\Program Files"),
            ("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
            ("LOCALAPPDATA", None),  # Will skip if not set
        ]
        windows_browsers = [
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("Microsoft", "Edge", "Application", "msedge.exe"),
        ]

        for env_var, default in env_vars:
            base_path_str = os.environ.get(env_var, default)
            if not base_path_str:
                continue  # Skip if env var not set and no default

            base_path = Path(base_path_str)
            for vendor, browser, app_dir, executable in windows_browsers:
                chrome_path = base_path / vendor / browser / app_dir / executable
                if chrome_path.exists():
                    return str(chrome_path)

        # Check Playwright-installed Chromium (Windows path)
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            playwright_cache = Path(localappdata) / "ms-playwright"
            if playwright_cache.exists():
                chromium_dirs = list(playwright_cache.glob("chromium-*"))
                for chromium_dir in chromium_dirs:
                    chrome_exe = chromium_dir / "chrome-win" / "chrome.exe"
                    if chrome_exe.exists():
                        return str(chrome_exe)

        return None
