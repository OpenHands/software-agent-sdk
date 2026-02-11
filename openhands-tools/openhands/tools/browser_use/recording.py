"""Recording session management for browser session recording using rrweb."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk import get_logger


if TYPE_CHECKING:
    from browser_use.browser.session import BrowserSession


logger = get_logger(__name__)

# Directory containing JavaScript files
_JS_DIR = Path(__file__).parent / "js"


# =============================================================================
# State Management
# =============================================================================


class RecordingState(Enum):
    """Explicit states for the recording session state machine."""

    IDLE = "idle"
    RECORDING = "recording"
    STOPPED = "stopped"


@dataclass
class EventBuffer:
    """Encapsulates event storage.

    This class manages the in-memory buffer of recording events.
    """

    events: list[dict] = field(default_factory=list)

    def add(self, event: dict) -> None:
        """Add a single event to the buffer."""
        self.events.append(event)

    def add_batch(self, events: list[dict]) -> None:
        """Add multiple events to the buffer."""
        self.events.extend(events)

    def clear(self) -> list[dict]:
        """Clear the buffer and return the events."""
        events = self.events
        self.events = []
        return events

    def __len__(self) -> int:
        """Return the number of events in the buffer."""
        return len(self.events)

    def __bool__(self) -> bool:
        """Return True if buffer has events."""
        return len(self.events) > 0


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
    """Encapsulates all recording state and logic for a browser session.

    This class manages the lifecycle of a recording session using a state machine
    pattern with explicit states (IDLE, RECORDING, STOPPED) and an EventBuffer
    for event storage.

    State Machine:
    - IDLE: Initial state, no recording active
    - RECORDING: Actively recording events
    - STOPPED: Recording has been stopped

    Concurrency (asyncio tasks):
    - Uses asyncio.Lock (_event_buffer_lock) to protect the event buffer and
      file operations from concurrent task access
    - The lock specifically protects: _event_buffer, _files_written, _total_events
    - The periodic flush loop and navigation-triggered flushes both acquire
      the lock before modifying the event buffer or saving to disk
    - Other state (_state, _flush_task, _scripts_injected) is not protected
      by this lock as these are only modified during start/stop transitions

    Directory Structure:
    - output_dir: Root directory where all recording sessions are stored
    - session_dir: Timestamped subfolder for the current recording session
    - Format: {output_dir}/recording-{timestamp}/
    - This ensures multiple start/stop cycles create separate folders
    """

    # Root directory for all recordings - each session creates a subfolder
    output_dir: str | None = None
    config: RecordingConfig = field(default_factory=lambda: DEFAULT_CONFIG)

    # Directory for current recording session (timestamped subfolder under output_dir)
    _session_dir: str | None = field(default=None, repr=False)

    # State machine
    _state: RecordingState = RecordingState.IDLE
    _event_buffer: EventBuffer = field(default_factory=EventBuffer)

    # File management
    _files_written: int = 0  # Count of files actually written this session
    _total_events: int = 0

    # Background task
    _flush_task: asyncio.Task | None = field(default=None, repr=False)

    # Browser state
    _scripts_injected: bool = False

    # Concurrency control - protects _event_buffer, _files_written, _total_events
    _event_buffer_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def session_dir(self) -> str | None:
        """Get the directory for the current recording session."""
        return self._session_dir

    def _create_session_subfolder(self) -> str | None:
        """Create a timestamped subfolder for this recording session.

        Returns:
            Path to the created subfolder, or None if output_dir is not set.
        """
        if not self.output_dir:
            return None

        # Generate timestamp in ISO format (safe for filenames)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        subfolder = os.path.join(self.output_dir, f"recording-{timestamp}")
        os.makedirs(subfolder, exist_ok=True)
        return subfolder

    @property
    def is_active(self) -> bool:
        """Check if recording is currently active."""
        return self._state == RecordingState.RECORDING

    @property
    def total_events(self) -> int:
        """Get total number of events recorded across all files."""
        return self._total_events

    @property
    def file_count(self) -> int:
        """Get the number of files saved this session."""
        return self._files_written

    @property
    def state(self) -> RecordingState:
        """Get the current recording state."""
        return self._state

    @property
    def event_buffer(self) -> EventBuffer:
        """Get the event buffer."""
        return self._event_buffer

    def save_events_to_file(self) -> str | None:
        """Save current events to a timestamped JSON file.

        Uses timestamps for filenames to avoid any file scanning or counter management.

        Returns:
            Path to the saved file, or None if session_dir is not set or no events.
        """
        if not self._session_dir or not self._event_buffer:
            return None

        os.makedirs(self._session_dir, exist_ok=True)

        # Use timestamp for filename - naturally unique and sortable
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        filename = f"{timestamp}.json"
        filepath = os.path.join(self._session_dir, filename)

        events = self._event_buffer.events
        with open(filepath, "w") as f:
            json.dump(events, f)

        self._files_written += 1
        self._total_events += len(events)
        logger.debug(
            f"Saved {len(events)} events to {filename} "
            f"(total: {self._total_events} events in {self._files_written} files)"
        )

        self._event_buffer.clear()
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
            logger.info("Injected rrweb loader script into browser session")
        except Exception as e:
            logger.warning(f"Failed to inject rrweb scripts: {e}")

        return script_ids

    async def flush_events(self, browser_session: BrowserSession) -> int:
        """Flush recording events from browser to Python storage.

        This collects events from the browser and adds them to the EventBuffer.
        Events are saved to disk by the periodic flush loop or when recording stops.

        Concurrency:
            Acquires _event_buffer_lock to protect the event buffer from
            concurrent task access (periodic flush loop vs navigation flushes).

        Returns:
            Number of events flushed.
        """
        if self._state != RecordingState.RECORDING:
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
                async with self._event_buffer_lock:
                    self._event_buffer.add_batch(events)
                    logger.debug(f"Flushed {len(events)} recording events from browser")

            return len(events)
        except Exception as e:
            logger.warning(f"Failed to flush recording events: {e}")
            return 0

    async def _periodic_flush_loop(self, browser_session: BrowserSession) -> None:
        """Background task that periodically flushes recording events.

        Concurrency:
            Acquires _event_buffer_lock when saving events to disk, coordinating
            with navigation-triggered flushes to prevent concurrent modifications
            to _event_buffer, _files_written, and _total_events.
        """
        while self._state == RecordingState.RECORDING:
            await asyncio.sleep(self.config.flush_interval_seconds)
            if self._state != RecordingState.RECORDING:
                break

            try:
                # Flush events from browser to Python storage (lock is acquired inside)
                await self.flush_events(browser_session)

                # Save to disk if we have any events (periodic save)
                async with self._event_buffer_lock:
                    if self._event_buffer:
                        self.save_events_to_file()
            except Exception as e:
                logger.warning(f"Periodic flush failed: {e}")

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
            logger.warning(
                f"Timeout waiting for rrweb to load "
                f"(timeout: {self.config.rrweb_load_timeout_ms}ms)"
            )
            return {"success": False, "error": "timeout"}

    async def start(self, browser_session: BrowserSession) -> str:
        """Start rrweb session recording.

        Uses event-driven Promise-based waiting for rrweb to load, avoiding
        polling anti-patterns. This waits exactly as long as needed and fails
        immediately if loading fails.

        Each recording session creates a new timestamped subfolder under output_dir
        to ensure multiple start/stop cycles don't mix events.

        Returns:
            Status message indicating success or failure.
        """
        # Inject scripts if not already done
        if not self._scripts_injected:
            await self.inject_scripts(browser_session)

        # Reset state for new recording session
        self._event_buffer.clear()
        self._state = RecordingState.RECORDING
        self._files_written = 0
        self._total_events = 0

        # Create a new timestamped subfolder for this recording session
        self._session_dir = self._create_session_subfolder()

        try:
            cdp_session = await browser_session.get_or_create_cdp_session()

            # Wait for rrweb to load using event-driven Promise
            load_result = await self._wait_for_rrweb_load(browser_session)

            if not load_result.get("success"):
                error = load_result.get("error", "unknown")
                self._state = RecordingState.IDLE
                await self._set_recording_flag(browser_session, False)

                if error == "load_failed":
                    logger.error(
                        "Unable to start recording: rrweb failed to load from CDN"
                    )
                    return (
                        "Error: Unable to start recording. The rrweb library "
                        "failed to load from CDN. Please check network "
                        "connectivity and try again."
                    )
                elif error == "timeout":
                    logger.error(
                        f"Unable to start recording: rrweb did not load within "
                        f"{self.config.rrweb_load_timeout_ms}ms"
                    )
                    return (
                        "Error: Unable to start recording. rrweb did not load in time. "
                        "Please navigate to a page first and try again."
                    )
                elif error == "not_injected":
                    logger.error("Unable to start recording: scripts not injected")
                    return (
                        "Error: Unable to start recording. Scripts not injected. "
                        "Please navigate to a page first and try again."
                    )
                else:
                    return f"Error: Unable to start recording: {error}"

            # rrweb is loaded, now start recording
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={"expression": _get_start_recording_js(), "returnByValue": True},
                session_id=cdp_session.session_id,
            )

            value = result.get("result", {}).get("value", {})
            status = value.get("status") if isinstance(value, dict) else value

            if status == "started":
                await self._set_recording_flag(browser_session, True)
                self._flush_task = asyncio.create_task(
                    self._periodic_flush_loop(browser_session)
                )
                logger.info("Recording started successfully with rrweb")
                return "Recording started"

            elif status == "already_recording":
                await self._set_recording_flag(browser_session, True)
                if not self._flush_task:
                    self._flush_task = asyncio.create_task(
                        self._periodic_flush_loop(browser_session)
                    )
                    logger.info("Recording already active, started periodic flush task")
                return "Already recording"

            elif status == "load_failed":
                self._state = RecordingState.IDLE
                await self._set_recording_flag(browser_session, False)
                logger.error("Unable to start recording: rrweb failed to load from CDN")
                return (
                    "Error: Unable to start recording. The rrweb library "
                    "failed to load from CDN. Please check network "
                    "connectivity and try again."
                )

            else:
                self._state = RecordingState.IDLE
                return f"Unknown status: {status}"

        except Exception as e:
            self._state = RecordingState.IDLE
            logger.exception("Error starting recording", exc_info=e)
            return f"Error starting recording: {str(e)}"

    async def stop(self, browser_session: BrowserSession) -> str:
        """Stop rrweb recording and save remaining events.

        Stops the periodic flush task, collects any remaining events from the
        browser, and saves them to a final numbered JSON file.

        Returns:
            A summary message with the save directory and file count.
        """
        if self._state != RecordingState.RECORDING:
            return "Error: Not recording. Call browser_start_recording first."

        try:
            # Stop the periodic flush task first
            self._state = RecordingState.STOPPED
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

            # Acquire lock for final event processing to ensure consistency
            async with self._event_buffer_lock:
                # Add current page events to the buffer
                if current_page_events:
                    self._event_buffer.add_batch(current_page_events)

                # Save any remaining events to a final file
                if self._event_buffer:
                    self.save_events_to_file()

                # Calculate totals while holding the lock
                total_events = self._total_events
                total_files = self._files_written

            await self._set_recording_flag(browser_session, False)
            session_dir_used = self.session_dir

            logger.info(
                f"Recording stopped: {total_events} events saved to "
                f"{total_files} file(s) in {session_dir_used}"
            )

            # Return a concise summary message
            summary = (
                f"Recording stopped. Captured {total_events} events "
                f"in {total_files} file(s)."
            )
            if session_dir_used:
                summary += f" Saved to: {session_dir_used}"

            return summary

        except Exception as e:
            self._state = RecordingState.STOPPED
            if self._flush_task:
                self._flush_task.cancel()
                self._flush_task = None
            logger.exception("Error stopping recording", exc_info=e)
            return f"Error stopping recording: {str(e)}"

    async def restart_on_new_page(self, browser_session: BrowserSession) -> None:
        """Restart recording on a new page after navigation.

        Uses event-driven Promise-based waiting for rrweb to be ready,
        then starts a new recording session. Called automatically after
        navigation when recording is active.
        """
        if self._state != RecordingState.RECORDING:
            return

        try:
            # Wait for rrweb to load using event-driven Promise
            load_result = await self._wait_for_rrweb_load(browser_session)

            if not load_result.get("success"):
                error = load_result.get("error", "unknown")
                logger.warning(
                    f"Could not restart recording on new page: rrweb {error}"
                )
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
                logger.warning(f"Unexpected status restarting recording: {status}")

        except Exception as e:
            logger.warning(f"Failed to restart recording on new page: {e}")

    def reset(self) -> None:
        """Reset the recording session state for reuse."""
        self._event_buffer.clear()
        self._state = RecordingState.IDLE
        self._session_dir = None  # Clear the current session's directory
        self._files_written = 0
        self._total_events = 0
        self._flush_task = None
        # Note: _scripts_injected is NOT reset - scripts persist in browser session
        # Note: output_dir is NOT reset - it's the root dir for all recordings
