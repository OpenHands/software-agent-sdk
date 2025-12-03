import os
import shutil
import sys
from pathlib import Path


_PLATFORM_SPECS = {
    "win32": {
        "system_paths": [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google/Chrome/Application/chrome.exe",
        ],
        "system_binaries": ["chrome.exe", "chromium.exe"],
        # Playwright Specifics for Windows
        "pw_local_cache": Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright",
        "pw_binary_suffix": Path("chrome-win/chrome.exe"),
    },
    "darwin": {
        "system_paths": [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ],
        "system_binaries": ["google-chrome", "chrome", "chromium"],
        # Playwright Specifics for macOS
        "pw_local_cache": Path.home() / "Library/Caches/ms-playwright",
        "pw_binary_suffix": Path("chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
    },
    "linux": {
        "system_paths": [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/chromium"),
        ],
        "system_binaries": ["google-chrome", "chromium", "chromium-browser"],
        # Playwright Specifics for Linux
        "pw_local_cache": Path.home() / ".cache" / "ms-playwright",
        "pw_binary_suffix": Path("chrome-linux/chrome"),
    },
}


def _get_playwright_path(spec: dict) -> Path | None:
    """
    Scans Playwright directories for the latest installed Chromium.
    """
    # 1. Determine the root cache directory
    cache_root = spec["pw_local_cache"]
    if not cache_root.exists():
        return None

    # 2. Find all chromium-* folders (e.g., chromium-1091, chromium-1080)
    #    Sort by name (descending) to try the latest version first.
    chromium_dirs = sorted(cache_root.glob("chromium-*"), reverse=True)

    # 3. Check inside for the OS-specific binary
    for d in chromium_dirs:
        binary_path = d / spec["pw_binary_suffix"]
        if binary_path.exists():
            return binary_path

    return None


def get_chromium_path() -> str:
    """Systematically finds the best available Chrome/Chromium binary."""
    # Setup
    current_os = sys.platform
    if current_os not in _PLATFORM_SPECS:
        current_os = "linux"

    spec = _PLATFORM_SPECS[current_os]

    # Priority 1: Playwright Installation (Most reliable for automation)
    if pw_path := _get_playwright_path(spec):
        return str(pw_path)

    # Priority 2: Standard System Paths
    for raw_path in spec["system_paths"]:
        if raw_path.exists():
            return str(raw_path)

    # Priority 3: PATH check
    for binary in spec["system_binaries"]:
        if path := shutil.which(binary):
            return path

    error_msg = (
        "Chromium is required for browser operations but is not installed.\n\n"
        "To install Chromium, run one of the following commands:\n"
        "  1. Using uvx (recommended): uvx playwright install chromium "
        "--with-deps --no-shell\n"
        "  2. Using pip: pip install playwright && playwright install chromium\n"
        "  3. Using system package manager:\n"
        "     - Ubuntu/Debian: sudo apt install chromium-browser\n"
        "     - macOS: brew install chromium\n"
        "     - Windows: winget install Chromium.Chromium\n\n"
        "After installation, restart your application to use the browser tool."
    )
    raise Exception(error_msg)
