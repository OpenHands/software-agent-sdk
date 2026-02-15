"""Recording session management for browser session recording using rrweb.

Error Handling Policy
=====================
Recording is a secondary feature that should never block primary browser operations.
This module follows a consistent error handling strategy based on operation type:

1. **User-facing operations** (start, stop):
   - Return descriptive error strings to the user (prefixed with "Error:")
   - Log at WARNING level for unexpected errors
   - Log at INFO level for expected failures (e.g., rrweb load failures)

2. **Internal/background operations** (flush_events, periodic flush, restart):
   - Log at DEBUG level and continue silently
   - Never raise exceptions that would interrupt browser operations
   - Return neutral values (0, None) on failure

3. **AttributeError for "not initialized"**:
   - Silent pass - this is expected when recording hasn't been set up
   - Used in the recording_aware decorator in impl.py

This policy ensures that recording failures are observable through logs but never
disrupt the user's primary browser workflow.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk import get_logger
from openhands.tools.browser_use.event_storage import EventStorage


if TYPE_CHECKING:
    from browser_use.browser.session import BrowserSession


logger = get_logger(__name__)

# Directory containing JavaScript files
_JS_DIR = Path(__file__).parent / "js"


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class RecordingConfig:
    """Configuration for recording sessions.

    CDN Dependency Note:
        The cdn_url points to unpkg.com which serves npm packages. If this CDN
        is unavailable (down, blocked by firewall, or slow), recording will fail
        to start. For production deployments in restricted environments, consider:
        - Self-hosting the rrweb library
        - Using a different CDN (jsdelivr, cdnjs)
        - Bundling rrweb with your application
    """

    flush_interval_seconds: float = 5.0
    rrweb_load_timeout_ms: int = 10000  # Timeout for rrweb to load from CDN
    cdn_url: str = "https://unpkg.com/rrweb@2.0.0-alpha.17/dist/rrweb.umd.cjs"


# Default configuration
DEFAULT_CONFIG = RecordingConfig()


# =============================================================================
# JavaScript Code Loading
# =============================================================================


@lru_cache(maxsize=16)
def _load_js_file(filename: str) -> str:
    """Load a JavaScript file from the js/ directory with caching."""
    filepath = _JS_DIR / filename
    return filepath.read_text()


def get_rrweb_loader_js(cdn_url: str) -> str:
    """Generate the rrweb loader JavaScript with the specified CDN URL."""
    template = _load_js_file("rrweb-loader.js")
    return template.replace("{{CDN_URL}}", cdn_url)


def _get_flush_events_js() -> str:
    """Get the JavaScript to flush recording events from browser to Python."""
    return _load_js_file("flush-events.js")


def _get_start_recording_simple_js() -> str:
    """Get the JavaScript to start recording on a page (simple version)."""
    return _load_js_file("start-recording-simple.js")


def _get_start_recording_js() -> str:
    """Get the JavaScript to start recording (full version with load failure check)."""
    return _load_js_file("start-recording.js")


def _get_stop_recording_js() -> str:
    """Get the JavaScript to stop recording and collect remaining events."""
    return _load_js_file("stop-recording.js")


def _get_wait_for_rrweb_js() -> str:
    """Get the JavaScript to wait for rrweb to load using Promise."""
    return _load_js_file("wait-for-rrweb.js")


# =============================================================================
# RecordingSession Class
# =============================================================================


@dataclass
class RecordingSession:
    """Manages browser session recording using rrweb.

    Concurrency: Uses asyncio.Lock to protect _events buffer from concurrent
    access by the periodic flush loop and navigation flushes.
    """

    output_dir: str | None = None
    config: RecordingConfig = field(default_factory=lambda: DEFAULT_CONFIG)

    _storage: EventStorage = field(default_factory=EventStorage, repr=False)
    _is_recording: bool = False
    _events: list[dict] = field(default_factory=list)
    _flush_task: asyncio.Task | None = field(default=None, repr=False)
    _scripts_injected: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _consecutive_flush_failures: int = 0

    def __post_init__(self) -> None:
        # Sync output_dir to storage
        self._storage.output_dir = self.output_dir

    @property
    def session_dir(self) -> str | None:
        return self._storage.session_dir

    @property
    def is_active(self) -> bool:
        return self._is_recording

    @property
    def total_events(self) -> int:
        return self._storage.total_events

    @property
    def file_count(self) -> int:
        return self._storage.file_count

    @property
    def events(self) -> list[dict]:
        return self._events

    def _save_and_clear_events(self) -> str | None:
        """Save current events to storage and clear the buffer."""
        if not self._events:
            return None
        filepath = self._storage.save_events(self._events)
        if filepath:
            self._events = []
        return filepath

    async def _set_recording_flag(
        self, browser_session: BrowserSession, should_record: bool
    ) -> None:
        """Set the recording flag in the browser for auto-start on new pages."""
        try:
            cdp_session = await browser_session.get_or_create_cdp_session()
            flag_value = str(should_record).lower()
            await cdp_session.cdp_client.send.Runtime.evaluate(
                params={
                    "expression": f"window.__rrweb_should_record = {flag_value};",
                    "returnByValue": True,
                },
                session_id=cdp_session.session_id,
            )
        except Exception as e:
            # Internal op: log at DEBUG, don't interrupt (see Error Handling Policy)
            logger.debug(f"Failed to set recording flag: {e}")

    async def inject_scripts(self, browser_session: BrowserSession) -> list[str]:
        """Inject rrweb loader script into the browser session.

        Uses Page.addScriptToEvaluateOnNewDocument to inject scripts that
        will run on every new document before the page's scripts execute.

        Returns:
            List of script identifiers returned by CDP.
        """
        if self._scripts_injected:
            return []

        script_ids = []
        try:
            cdp_session = await browser_session.get_or_create_cdp_session()
            cdp_client = cdp_session.cdp_client

            rrweb_loader = get_rrweb_loader_js(self.config.cdn_url)
            result = await cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
                params={"source": rrweb_loader, "runImmediately": True},
                session_id=cdp_session.session_id,
            )
            script_id = result.get("identifier")
            if script_id:
                script_ids.append(script_id)
                logger.debug(f"Injected rrweb script with identifier: {script_id}")

            self._scripts_injected = True
            logger.debug("Injected rrweb loader script")
        except Exception as e:
            # Internal op: log at DEBUG, don't interrupt (see Error Handling Policy)
            logger.debug(f"Script injection skipped: {e}")

        return script_ids

    async def flush_events(self, browser_session: BrowserSession) -> int:
        """Flush recording events from browser to Python storage."""
        if not self._is_recording:
            return 0

        try:
            cdp_session = await browser_session.get_or_create_cdp_session()
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={"expression": _get_flush_events_js(), "returnByValue": True},
                session_id=cdp_session.session_id,
            )

            data = json.loads(result.get("result", {}).get("value", "{}"))
            events = data.get("events", [])
            if events:
                async with self._lock:
                    self._events.extend(events)
                    logger.debug(f"Flushed {len(events)} events from browser")

            return len(events)
        except Exception as e:
            # Internal op: log at DEBUG, return 0 (see Error Handling Policy)
            logger.debug(f"Event flush skipped: {e}")
            return 0

    async def _periodic_flush_loop(self, browser_session: BrowserSession) -> None:
        """Background task that periodically flushes recording events."""
        while self._is_recording:
            await asyncio.sleep(self.config.flush_interval_seconds)
            if not self._is_recording:
                break

            try:
                await self.flush_events(browser_session)
                async with self._lock:
                    if self._events:
                        filepath = self._save_and_clear_events()
                        if filepath:
                            self._consecutive_flush_failures = 0
                        else:
                            self._consecutive_flush_failures += 1
            except Exception as e:
                # Internal op: log at DEBUG, don't interrupt (see Error Handling Policy)
                self._consecutive_flush_failures += 1
                logger.debug(f"Periodic flush skipped: {e}")

            # Warn after 3 consecutive failures for visibility into persistent issues
            if self._consecutive_flush_failures >= 3:
                logger.warning(
                    f"Recording flush has failed {self._consecutive_flush_failures} "
                    f"times. Events may be accumulating in memory. "
                    f"Check disk space and permissions."
                )

    async def _wait_for_rrweb_load(self, browser_session: BrowserSession) -> dict:
        """Wait for rrweb to load using event-driven Promise-based waiting.

        Uses CDP's awaitPromise to wait for the rrweb loader Promise to resolve,
        avoiding polling anti-patterns. This waits exactly as long as needed
        and fails immediately if loading fails.

        Returns:
            Dict with 'success' (bool) and optionally 'error' (str) keys.
        """
        cdp_session = await browser_session.get_or_create_cdp_session()

        try:
            result = await asyncio.wait_for(
                cdp_session.cdp_client.send.Runtime.evaluate(
                    params={
                        "expression": _get_wait_for_rrweb_js(),
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                    session_id=cdp_session.session_id,
                ),
                timeout=self.config.rrweb_load_timeout_ms / 1000,
            )

            value = result.get("result", {}).get("value", {})
            if isinstance(value, dict):
                return value
            return {"success": False, "error": "unexpected_response"}

        except TimeoutError:
            logger.debug(f"rrweb load timeout ({self.config.rrweb_load_timeout_ms}ms)")
            return {"success": False, "error": "timeout"}

    def _initialize_session_state(self) -> None:
        """Reset state and create session subfolder for a new recording session."""
        self._events = []
        self._is_recording = True
        self._consecutive_flush_failures = 0
        self._storage.reset()
        self._storage.output_dir = self.output_dir
        self._storage.create_session_subfolder()

    async def _handle_rrweb_load_failure(
        self, browser_session: BrowserSession, error: str
    ) -> str:
        """Handle rrweb load failure and return appropriate error message.

        Expected failure: log at INFO, return error string (see Error Handling Policy)
        """
        self._is_recording = False
        await self._set_recording_flag(browser_session, False)

        error_messages = {
            "load_failed": (
                "Error: Unable to start recording. The rrweb library "
                "failed to load from CDN. Please check network "
                "connectivity and try again."
            ),
            "timeout": (
                "Error: Unable to start recording. rrweb did not load in time. "
                "Please navigate to a page first and try again."
            ),
            "not_injected": (
                "Error: Unable to start recording. Scripts not injected. "
                "Please navigate to a page first and try again."
            ),
        }

        if error in error_messages:
            if error == "timeout":
                logger.info(
                    f"Recording start failed: rrweb load timeout "
                    f"({self.config.rrweb_load_timeout_ms}ms)"
                )
            else:
                logger.info(f"Recording start failed: rrweb {error}")
            return error_messages[error]

        logger.info(f"Recording start failed: {error}")
        return f"Error: Unable to start recording: {error}"

    async def _ensure_rrweb_loaded(self, browser_session: BrowserSession) -> str | None:
        """Wait for rrweb to load. Returns error message if failed, None on success."""
        load_result = await self._wait_for_rrweb_load(browser_session)

        if not load_result.get("success"):
            error = load_result.get("error", "unknown")
            return await self._handle_rrweb_load_failure(browser_session, error)

        return None

    async def _start_flush_task(self, browser_session: BrowserSession) -> None:
        """Start the periodic flush task if not already running."""
        if not self._flush_task:
            self._flush_task = asyncio.create_task(
                self._periodic_flush_loop(browser_session)
            )

    async def _execute_start_recording(self, browser_session: BrowserSession) -> str:
        """Execute the start recording JS and handle the result status."""
        cdp_session = await browser_session.get_or_create_cdp_session()

        result = await cdp_session.cdp_client.send.Runtime.evaluate(
            params={"expression": _get_start_recording_js(), "returnByValue": True},
            session_id=cdp_session.session_id,
        )

        value = result.get("result", {}).get("value", {})
        status = value.get("status") if isinstance(value, dict) else value

        if status == "started":
            await self._set_recording_flag(browser_session, True)
            await self._start_flush_task(browser_session)
            logger.info("Recording started")
            return "Recording started"

        if status == "already_recording":
            await self._set_recording_flag(browser_session, True)
            await self._start_flush_task(browser_session)
            logger.debug("Recording already active")
            return "Already recording"

        if status == "load_failed":
            return await self._handle_rrweb_load_failure(browser_session, "load_failed")

        self._is_recording = False
        logger.info(f"Recording start failed: unknown status '{status}'")
        return f"Unknown status: {status}"

    async def start(self, browser_session: BrowserSession) -> str:
        """Start rrweb session recording.

        Uses event-driven Promise-based waiting for rrweb to load, avoiding
        polling anti-patterns. This waits exactly as long as needed and fails
        immediately if loading fails.

        Each recording session creates a new timestamped subfolder under output_dir
        to ensure multiple start/stop cycles don't mix events.

        Returns:
            Status message indicating success or failure.

        Note:
            User-facing operation: returns error strings, logs at WARNING for
            unexpected errors (see Error Handling Policy in module docstring).
        """
        if not self._scripts_injected:
            await self.inject_scripts(browser_session)

        self._initialize_session_state()

        try:
            error_msg = await self._ensure_rrweb_loaded(browser_session)
            if error_msg:
                return error_msg

            return await self._execute_start_recording(browser_session)

        except Exception as e:
            # User-facing operation: log at WARNING, return error string
            self._is_recording = False
            logger.warning(f"Recording start failed: {e}")
            return f"Error starting recording: {str(e)}"

    async def stop(self, browser_session: BrowserSession) -> str:
        """Stop rrweb recording and save remaining events.

        Stops the periodic flush task, collects any remaining events from the
        browser, and saves them to a final numbered JSON file.

        Returns:
            A summary message with the save directory and file count.

        Note:
            User-facing operation: returns error strings, logs at WARNING for
            unexpected errors (see Error Handling Policy in module docstring).
        """
        if not self._is_recording:
            return "Error: Not recording. Call browser_start_recording first."

        try:
            # Stop the periodic flush task first
            self._is_recording = False
            if self._flush_task:
                self._flush_task.cancel()
                try:
                    await self._flush_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._flush_task = None

            cdp_session = await browser_session.get_or_create_cdp_session()

            # Stop recording on current page and get remaining events
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={"expression": _get_stop_recording_js(), "returnByValue": True},
                session_id=cdp_session.session_id,
            )

            current_page_data = json.loads(result.get("result", {}).get("value", "{}"))
            current_page_events = current_page_data.get("events", [])

            async with self._lock:
                if current_page_events:
                    self._events.extend(current_page_events)
                if self._events:
                    self._save_and_clear_events()
                total_events = self._storage.total_events
                total_files = self._storage.file_count

            await self._set_recording_flag(browser_session, False)
            session_dir_used = self._storage.session_dir

            logger.info(
                f"Recording stopped: {total_events} events saved to "
                f"{total_files} file(s) in {session_dir_used}"
            )

            summary = (
                f"Recording stopped. Captured {total_events} events "
                f"in {total_files} file(s)."
            )
            if session_dir_used:
                summary += f" Saved to: {session_dir_used}"

            return summary

        except Exception as e:
            # User-facing operation: log at WARNING, return error string
            self._is_recording = False
            if self._flush_task:
                self._flush_task.cancel()
                self._flush_task = None
            logger.warning(f"Recording stop failed: {e}")
            return f"Error stopping recording: {str(e)}"

    async def restart_on_new_page(self, browser_session: BrowserSession) -> None:
        """Restart recording on a new page after navigation.

        Uses event-driven Promise-based waiting for rrweb to be ready,
        then starts a new recording session. Called automatically after
        navigation when recording is active.

        Note:
            Internal operation: logs at DEBUG, never raises
            (see Error Handling Policy in module docstring).
        """
        if not self._is_recording:
            return

        try:
            load_result = await self._wait_for_rrweb_load(browser_session)

            if not load_result.get("success"):
                error = load_result.get("error", "unknown")
                logger.debug(f"Recording restart skipped: rrweb {error}")
                return

            cdp_session = await browser_session.get_or_create_cdp_session()
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={
                    "expression": _get_start_recording_simple_js(),
                    "returnByValue": True,
                },
                session_id=cdp_session.session_id,
            )

            value = result.get("result", {}).get("value", {})
            status = value.get("status") if isinstance(value, dict) else value

            if status == "started":
                logger.debug("Recording restarted on new page")
            elif status == "already_recording":
                logger.debug("Recording already active on new page")
            else:
                logger.debug(f"Recording restart: unexpected status '{status}'")

        except Exception as e:
            # Internal op: log at DEBUG, don't interrupt (see Error Handling Policy)
            logger.debug(f"Recording restart skipped: {e}")

    def reset(self) -> None:
        """Reset the recording session state for reuse."""
        self._events = []
        self._is_recording = False
        self._storage.reset()
        self._flush_task = None
