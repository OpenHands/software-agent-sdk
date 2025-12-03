"""Windows-specific browser tool executor implementation."""

import os
from pathlib import Path

from openhands.sdk.logger import get_logger
from openhands.tools.browser_use.impl import BrowserToolExecutor


logger = get_logger(__name__)


def _check_chromium_available_windows() -> str | None:
    """Check if a Chromium/Chrome binary is available on Windows.

    Checks common Windows installation paths for Chrome and Edge executables.
    Also checks Playwright cache detection on Windows using LOCALAPPDATA.
    """
    import shutil

    # First check if chromium/chrome is in PATH
    for binary in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        if path := shutil.which(binary):
            return path

    # Check common Windows installation paths
    windows_chrome_paths = []
    env_vars = [
        ("PROGRAMFILES", "C:\\Program Files"),
        ("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
        ("LOCALAPPDATA", ""),
    ]
    windows_browsers = [
        ("Google", "Chrome", "Application", "chrome.exe"),
        ("Microsoft", "Edge", "Application", "msedge.exe"),
    ]

    for env_var, default in env_vars:
        for vendor, browser, app_dir, executable in windows_browsers:
            base_path_str = os.environ.get(env_var, default)
            if base_path_str:
                base_path = Path(base_path_str)
                windows_chrome_paths.append(
                    base_path / vendor / browser / app_dir / executable
                )
    for chrome_path in windows_chrome_paths:
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


class WindowsBrowserToolExecutor(BrowserToolExecutor):
    """Windows-specific browser tool executor with Chromium detection."""

    def __init__(
        self,
        headless: bool = True,
        allowed_domains: list[str] | None = None,
        session_timeout_minutes: int = 30,
        init_timeout_seconds: int = 30,
        full_output_save_dir: str | None = None,
        **config,
    ):
        """Initialize WindowsBrowserToolExecutor with Windows-specific logic.

        Args:
            headless: Whether to run browser in headless mode
            allowed_domains: List of allowed domains for browser operations
            session_timeout_minutes: Browser session timeout in minutes
            init_timeout_seconds: Timeout for browser initialization in seconds
            full_output_save_dir: Absolute path to directory to save full output
            logs and files, used when truncation is needed.
            **config: Additional configuration options
        """
        # Temporarily override the module-level function for Windows
        import openhands.tools.browser_use.impl as impl_module

        original_check = impl_module._check_chromium_available
        impl_module._check_chromium_available = _check_chromium_available_windows

        try:
            # Call parent constructor which will use our Windows-specific check
            super().__init__(
                headless=headless,
                allowed_domains=allowed_domains,
                session_timeout_minutes=session_timeout_minutes,
                init_timeout_seconds=init_timeout_seconds,
                full_output_save_dir=full_output_save_dir,
                **config,
            )
        finally:
            # Restore original function (for cleanliness, though not strictly necessary)
            impl_module._check_chromium_available = original_check
