"""Browser tool executor implementation using browser-use MCP server wrapper."""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation

from openhands.sdk.logger import DEBUG, get_logger
from openhands.sdk.tool import ToolExecutor
from openhands.sdk.utils import sanitized_env
from openhands.sdk.utils.async_executor import AsyncExecutor
from openhands.tools.browser_use.definition import (
    BROWSER_RECORDING_OUTPUT_DIR,
    BrowserAction,
    BrowserObservation,
)
from openhands.tools.browser_use.server import CustomBrowserUseServer
from openhands.tools.utils.timeout import TimeoutError, run_with_timeout


F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])


def recording_aware(
    func: Callable[..., Coroutine[Any, Any, Any]],
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Decorator that handles recording flush before/after navigation operations.

    This decorator:
    1. Flushes recording events before the operation (to preserve them)
    2. Executes the operation
    3. Restarts recording on the new page if recording was active

    Error Handling Policy (see recording.py module docstring for full details):
    - Recording is a secondary feature that should never block browser operations
    - AttributeError: silent pass (recording not initialized - expected)
    - Other exceptions: log at DEBUG, don't interrupt navigation
    """

    @functools.wraps(func)
    async def wrapper(self: BrowserToolExecutor, *args: Any, **kwargs: Any) -> Any:
        is_recording = self._server._is_recording
        if is_recording:
            try:
                await self._server._flush_recording_events()
            except AttributeError:
                # Recording not initialized - expected, silent pass
                pass
            except Exception as e:
                # Internal operation: log at DEBUG, don't interrupt navigation
                logger.debug(f"Recording flush before {func.__name__} skipped: {e}")

        result = await func(self, *args, **kwargs)

        if is_recording:
            try:
                await self._server._restart_recording_on_new_page()
            except AttributeError:
                # Recording not initialized - expected, silent pass
                pass
            except Exception as e:
                # Internal operation: log at DEBUG, don't interrupt navigation
                logger.debug(f"Recording restart after {func.__name__} skipped: {e}")

        return result

    return wrapper


# Suppress browser-use logging for cleaner integration
if DEBUG:
    logging.getLogger("browser_use").setLevel(logging.DEBUG)
else:
    logging.getLogger("browser_use").setLevel(logging.WARNING)

logger = get_logger(__name__)


def _install_chromium() -> bool:
    """Attempt to install Chromium via uvx playwright install."""
    try:
        # Check if uvx is available
        if not shutil.which("uvx"):
            logger.warning("uvx not found - cannot auto-install Chromium")
            return False

        logger.info("Attempting to install Chromium via uvx...")
        result = subprocess.run(
            ["uvx", "playwright", "install", "chromium", "--with-deps", "--no-shell"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes timeout for installation
            env=sanitized_env(),
        )

        if result.returncode == 0:
            logger.info("Chromium installation completed successfully")
            return True
        else:
            logger.error(f"Chromium installation failed: {result.stderr}")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.error(f"Error during Chromium installation: {e}")
        return False


def _get_chromium_error_message() -> str:
    """Get the error message for when Chromium is not available."""
    return (
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


class BrowserToolExecutor(ToolExecutor[BrowserAction, BrowserObservation]):
    """Executor that wraps browser-use MCP server for OpenHands integration."""

    _server: CustomBrowserUseServer
    _config: dict[str, Any]
    _initialized: bool
    _async_executor: AsyncExecutor
    _cleanup_initiated: bool

    def check_chromium_available(self) -> str | None:
        """Check if a Chromium/Chrome binary is available.

        This method can be overridden by subclasses to provide
        platform-specific detection logic.

        Returns:
            Path to Chromium binary if found, None otherwise
        """
        for binary in ("chromium", "chromium-browser", "google-chrome", "chrome"):
            if path := shutil.which(binary):
                return path

        # Check standard installation paths
        standard_paths = [
            # Linux
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            # macOS
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
        for install_path in standard_paths:
            p = Path(install_path)
            if p.exists():
                return str(p)

        # Check Playwright-installed Chromium
        playwright_cache_candidates = [
            Path.home() / ".cache" / "ms-playwright",  # Linux
            Path.home() / "Library" / "Caches" / "ms-playwright",  # macOS
        ]

        for playwright_cache in playwright_cache_candidates:
            if playwright_cache.exists():
                chromium_dirs = list(playwright_cache.glob("chromium-*"))
                for chromium_dir in chromium_dirs:
                    # Check platform-specific paths
                    possible_paths = [
                        chromium_dir / "chrome-linux" / "chrome",  # Linux (old)
                        chromium_dir / "chrome-linux64" / "chrome",  # Linux (new)
                        chromium_dir
                        / "chrome-mac"
                        / "Chromium.app"
                        / "Contents"
                        / "MacOS"
                        / "Chromium",  # macOS
                    ]
                    for p in possible_paths:
                        if p.exists():
                            return str(p)
        return None

    def _ensure_chromium_available(self) -> str:
        """Ensure Chromium is available for browser operations.

        Raises:
            Exception: If Chromium is not available
        """
        if path := self.check_chromium_available():
            logger.info(f"Chromium is available for browser operations at {path}")
            return path

        # Chromium not available - provide clear installation instructions
        raise Exception(_get_chromium_error_message())

    def __init__(
        self,
        headless: bool = True,
        allowed_domains: list[str] | None = None,
        session_timeout_minutes: int = 30,
        init_timeout_seconds: int = 30,
        full_output_save_dir: str | None = None,
        inject_scripts: list[str] | None = None,
        **config,
    ):
        """Initialize BrowserToolExecutor with timeout protection.

        Args:
            headless: Whether to run browser in headless mode
            allowed_domains: List of allowed domains for browser operations
            session_timeout_minutes: Browser session timeout in minutes
            init_timeout_seconds: Timeout for browser initialization in seconds
            full_output_save_dir: Absolute path to directory to save full output
                logs and files, used when truncation is needed.
            inject_scripts: List of JavaScript code strings to inject into every
                new document. Scripts are injected via CDP's
                Page.addScriptToEvaluateOnNewDocument and run before page scripts.
                Useful for injecting recording tools like rrweb.
            **config: Additional configuration options
        """

        def init_logic():
            nonlocal headless
            executable_path = self._ensure_chromium_available()
            self._server = CustomBrowserUseServer(
                session_timeout_minutes=session_timeout_minutes,
            )
            if os.getenv("OH_ENABLE_VNC", "false").lower() in {"true", "1", "yes"}:
                headless = False  # Force headless off if VNC is enabled
                logger.info("VNC is enabled - running browser in non-headless mode")

            # Configure scripts to inject
            if inject_scripts:
                self._server.set_inject_scripts(inject_scripts)

            self._config = {
                "headless": headless,
                "allowed_domains": allowed_domains or [],
                "executable_path": executable_path,
                **config,
            }

        try:
            run_with_timeout(init_logic, init_timeout_seconds)
        except TimeoutError:
            raise Exception(
                f"Browser tool initialization timed out after {init_timeout_seconds}s"
            )

        self.full_output_save_dir: str | None = full_output_save_dir
        self._initialized = False
        self._async_executor = AsyncExecutor()
        self._cleanup_initiated = False

    def __call__(
        self,
        action: BrowserAction,
        conversation: LocalConversation | None = None,  # noqa: ARG002
    ):
        """Submit an action to run in the background loop and wait for result."""
        return self._async_executor.run_async(
            self._execute_action, action, timeout=300.0
        )

    async def _execute_action(self, action):
        """Execute browser action asynchronously."""
        from openhands.tools.browser_use.definition import (
            BrowserClickAction,
            BrowserCloseTabAction,
            BrowserGetContentAction,
            BrowserGetStateAction,
            BrowserGetStorageAction,
            BrowserGoBackAction,
            BrowserListTabsAction,
            BrowserNavigateAction,
            BrowserObservation,
            BrowserScrollAction,
            BrowserSetStorageAction,
            BrowserStartRecordingAction,
            BrowserStopRecordingAction,
            BrowserSwitchTabAction,
            BrowserTypeAction,
        )

        try:
            result = ""
            # Route to appropriate method based on action type
            if isinstance(action, BrowserNavigateAction):
                result = await self.navigate(action.url, action.new_tab)
            elif isinstance(action, BrowserClickAction):
                result = await self.click(action.index, action.new_tab)
            elif isinstance(action, BrowserTypeAction):
                result = await self.type_text(action.index, action.text)
            elif isinstance(action, BrowserGetStateAction):
                return await self.get_state(action.include_screenshot)
            elif isinstance(action, BrowserGetStorageAction):
                result = await self.get_storage()
            elif isinstance(action, BrowserSetStorageAction):
                result = await self.set_storage(action.storage_state)
            elif isinstance(action, BrowserGetContentAction):
                result = await self.get_content(
                    action.extract_links, action.start_from_char
                )
            elif isinstance(action, BrowserScrollAction):
                result = await self.scroll(action.direction)
            elif isinstance(action, BrowserGoBackAction):
                result = await self.go_back()
            elif isinstance(action, BrowserListTabsAction):
                result = await self.list_tabs()
            elif isinstance(action, BrowserSwitchTabAction):
                result = await self.switch_tab(action.tab_id)
            elif isinstance(action, BrowserCloseTabAction):
                result = await self.close_tab(action.tab_id)
            elif isinstance(action, BrowserStartRecordingAction):
                result = await self.start_recording()
            elif isinstance(action, BrowserStopRecordingAction):
                result = await self.stop_recording()
            else:
                error_msg = f"Unsupported action type: {type(action)}"
                return BrowserObservation.from_text(
                    text=error_msg,
                    is_error=True,
                    full_output_save_dir=self.full_output_save_dir,
                )

            return BrowserObservation.from_text(
                text=result,
                is_error=False,
                full_output_save_dir=self.full_output_save_dir,
            )
        except Exception as e:
            error_msg = f"Browser operation failed: {str(e)}"
            logging.error(error_msg, exc_info=True)
            return BrowserObservation.from_text(
                text=error_msg,
                is_error=True,
                full_output_save_dir=self.full_output_save_dir,
            )

    async def _ensure_initialized(self):
        """Ensure browser session is initialized."""
        if not self._initialized:
            # Initialize browser session with our config
            await self._server._init_browser_session(**self._config)
            # Inject any configured user scripts after session is ready
            # Note: rrweb scripts are injected lazily when recording starts
            await self._server._inject_scripts_to_session()
            self._initialized = True

    # Navigation & Browser Control Methods
    @recording_aware
    async def navigate(self, url: str, new_tab: bool = False) -> str:
        """Navigate to a URL."""
        await self._ensure_initialized()
        return await self._server._navigate(url, new_tab)

    @recording_aware
    async def go_back(self) -> str:
        """Go back in browser history."""
        await self._ensure_initialized()
        return await self._server._go_back()

    # Page Interaction
    @recording_aware
    async def click(self, index: int, new_tab: bool = False) -> str:
        """Click an element by index."""
        await self._ensure_initialized()
        return await self._server._click(index, new_tab)

    async def type_text(self, index: int, text: str) -> str:
        """Type text into an element."""
        await self._ensure_initialized()
        return await self._server._type_text(index, text)

    async def scroll(self, direction: str = "down") -> str:
        """Scroll the page."""
        await self._ensure_initialized()
        return await self._server._scroll(direction)

    async def get_state(self, include_screenshot: bool = False):
        """Get current browser state with interactive elements."""
        from openhands.tools.browser_use.definition import BrowserObservation

        await self._ensure_initialized()
        result_json = await self._server._get_browser_state(include_screenshot)

        if include_screenshot:
            try:
                result_data = json.loads(result_json)
                screenshot_data = result_data.pop("screenshot", None)

                # Return clean JSON + separate screenshot data
                clean_json = json.dumps(result_data, indent=2)
                return BrowserObservation.from_text(
                    text=clean_json,
                    is_error=False,
                    screenshot_data=screenshot_data,
                    full_output_save_dir=self.full_output_save_dir,
                )
            except json.JSONDecodeError:
                # If JSON parsing fails, return as-is
                pass

        return BrowserObservation.from_text(
            text=result_json,
            is_error=False,
            full_output_save_dir=self.full_output_save_dir,
        )

    async def get_storage(self) -> str:
        """Get browser storage (cookies, local storage, session storage)."""
        await self._ensure_initialized()
        return await self._server._get_storage()

    async def set_storage(self, storage_state: dict) -> str:
        """Set browser storage (cookies, local storage, session storage)."""
        await self._ensure_initialized()
        return await self._server._set_storage(storage_state)

    # Tab Management
    async def list_tabs(self) -> str:
        """List all open tabs."""
        await self._ensure_initialized()
        return await self._server._list_tabs()

    async def switch_tab(self, tab_id: str) -> str:
        """Switch to a different tab."""
        await self._ensure_initialized()
        return await self._server._switch_tab(tab_id)

    async def close_tab(self, tab_id: str) -> str:
        """Close a specific tab."""
        await self._ensure_initialized()
        return await self._server._close_tab(tab_id)

    # Content Extraction
    async def get_content(self, extract_links: bool, start_from_char: int) -> str:
        """Extract page content, optionally with links."""
        await self._ensure_initialized()
        return await self._server._get_content(
            extract_links=extract_links, start_from_char=start_from_char
        )

    # Session Recording
    async def start_recording(self) -> str:
        """Start recording the browser session using rrweb.

        Recording events are periodically flushed to timestamped JSON files
        in a session subfolder under BROWSER_RECORDING_OUTPUT_DIR.
        Events are flushed every 5 seconds.
        """
        await self._ensure_initialized()
        return await self._server._start_recording(
            output_dir=BROWSER_RECORDING_OUTPUT_DIR
        )

    async def stop_recording(self) -> str:
        """Stop recording and save remaining events to file.

        Stops the periodic flush, collects any remaining events, and saves
        them to a final numbered JSON file. Returns a summary message with
        the total events and file count.
        """
        await self._ensure_initialized()
        return await self._server._stop_recording()

    async def close_browser(self) -> str:
        """Close the browser session."""
        if self._initialized:
            result = await self._server._close_browser()
            self._initialized = False
            return result
        return "No browser session to close"

    async def cleanup(self):
        """Cleanup browser resources."""
        try:
            await self.close_browser()
            if hasattr(self._server, "_close_all_sessions"):
                await self._server._close_all_sessions()
        except Exception as e:
            logger.warning(f"Error during browser cleanup: {e}")

    def close(self):
        """Close the browser executor and cleanup resources."""
        if self._cleanup_initiated:
            return
        self._cleanup_initiated = True
        try:
            # Run cleanup in the async executor with a shorter timeout
            self._async_executor.run_async(self.cleanup, timeout=30.0)
        except Exception as e:
            logger.warning(f"Error during browser cleanup: {e}")
        finally:
            # Always close the async executor
            self._async_executor.close()

    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.close()
        except Exception:
            pass  # Ignore cleanup errors during deletion
