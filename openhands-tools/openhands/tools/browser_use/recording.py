"""Recording session management for browser session recording using rrweb."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from openhands.sdk import get_logger


if TYPE_CHECKING:
    from browser_use.browser.session import BrowserSession


logger = get_logger(__name__)


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
    """Encapsulates event storage and size tracking.

    This class manages the in-memory buffer of recording events,
    tracking both the events themselves and their cumulative size.
    """

    events: list[dict] = field(default_factory=list)
    size_bytes: int = 0

    def add(self, event: dict) -> None:
        """Add a single event to the buffer and update size."""
        self.events.append(event)
        self.size_bytes += len(json.dumps(event))

    def add_batch(self, events: list[dict]) -> None:
        """Add multiple events to the buffer."""
        for event in events:
            self.add(event)

    def should_flush(self, threshold_mb: float) -> bool:
        """Check if buffer size exceeds the threshold."""
        return self.size_bytes > threshold_mb * 1024 * 1024

    def clear(self) -> list[dict]:
        """Clear the buffer and return the events."""
        events = self.events
        self.events = []
        self.size_bytes = 0
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
    """Configuration for recording sessions."""

    flush_interval_seconds: float = 5.0
    flush_size_mb: float = 1.0
    start_max_retries: int = 10
    retry_delay_ms: int = 500
    max_file_counter: int = 100000  # Safety limit for filename counter
    cdn_url: str = "https://unpkg.com/rrweb@2.0.0-alpha.17/dist/rrweb.umd.cjs"


# Default configuration
DEFAULT_CONFIG = RecordingConfig()


# =============================================================================
# JavaScript Code
# =============================================================================


def get_rrweb_loader_js(cdn_url: str) -> str:
    """Generate the rrweb loader JavaScript with the specified CDN URL."""
    return (
        """
(function() {
    if (window.__rrweb_loaded) return;
    window.__rrweb_loaded = true;

    // Initialize storage for events (per-page, will be flushed to backend)
    window.__rrweb_events = window.__rrweb_events || [];
    // Flag to indicate if recording should auto-start on new pages (cross-page)
    // This is ONLY set after explicit start_recording call, not on initial load
    window.__rrweb_should_record = window.__rrweb_should_record || false;
    // Flag to track if rrweb failed to load
    window.__rrweb_load_failed = false;

    function loadRrweb() {
        var s = document.createElement('script');
        s.src = '"""
        + cdn_url
        + """';
        s.onload = function() {
            window.__rrweb_ready = true;
            console.log('[rrweb] Loaded successfully from CDN');
            // Auto-start recording ONLY if flag is set (for cross-page continuity)
            // This flag is only true after an explicit start_recording call
            if (window.__rrweb_should_record && !window.__rrweb_stopFn) {
                window.startRecordingInternal();
            }
        };
        s.onerror = function() {
            console.error('[rrweb] Failed to load from CDN');
            window.__rrweb_load_failed = true;
        };
        (document.head || document.documentElement).appendChild(s);
    }

    // Internal function to start recording (used for auto-start on navigation)
    window.startRecordingInternal = function() {
        var recordFn = (typeof rrweb !== 'undefined' && rrweb.record) ||
                       (typeof rrwebRecord !== 'undefined' && rrwebRecord.record);
        if (!recordFn || window.__rrweb_stopFn) return;

        window.__rrweb_events = [];
        window.__rrweb_stopFn = recordFn({
            emit: function(event) {
                window.__rrweb_events.push(event);
            }
        });
        console.log('[rrweb] Auto-started recording on new page');
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', loadRrweb);
    } else {
        loadRrweb();
    }
})();
"""
    )


# JavaScript to flush recording events from browser to Python
FLUSH_EVENTS_JS = """
(function() {
    var events = window.__rrweb_events || [];
    // Clear browser-side events after flushing
    window.__rrweb_events = [];
    return JSON.stringify({events: events});
})();
"""

# JavaScript to start recording on a page (used for restart after navigation)
START_RECORDING_SIMPLE_JS = """
(function() {
    var recordFn = (typeof rrweb !== 'undefined' && rrweb.record) ||
                   (typeof rrwebRecord !== 'undefined' && rrwebRecord.record);
    if (!recordFn) return {status: 'not_loaded'};
    if (window.__rrweb_stopFn) return {status: 'already_recording'};

    window.__rrweb_events = [];
    window.__rrweb_stopFn = recordFn({
        emit: function(event) {
            window.__rrweb_events.push(event);
        }
    });
    return {status: 'started'};
})();
"""

# JavaScript to start recording (full version with load failure check)
START_RECORDING_JS = """
(function() {
    if (window.__rrweb_stopFn) return {status: 'already_recording'};
    // Check if rrweb failed to load from CDN
    if (window.__rrweb_load_failed) return {status: 'load_failed'};
    // rrweb UMD module exports to window.rrweb (not rrwebRecord)
    var recordFn = (typeof rrweb !== 'undefined' && rrweb.record) ||
                   (typeof rrwebRecord !== 'undefined' && rrwebRecord.record);
    if (!recordFn) return {status: 'not_loaded'};
    window.__rrweb_events = [];
    window.__rrweb_should_record = true;
    window.__rrweb_stopFn = recordFn({
        emit: function(event) {
            window.__rrweb_events.push(event);
        }
    });
    return {status: 'started'};
})();
"""

# JavaScript to stop recording and collect remaining events
STOP_RECORDING_JS = """
(function() {
    var events = window.__rrweb_events || [];

    // Stop the recording if active
    if (window.__rrweb_stopFn) {
        window.__rrweb_stopFn();
        window.__rrweb_stopFn = null;
    }

    // Clear flags
    window.__rrweb_should_record = false;
    window.__rrweb_events = [];

    return JSON.stringify({events: events});
})();
"""


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

    Thread Safety:
    - Uses asyncio.Lock to protect flush operations from concurrent access
    - The periodic flush loop and navigation-triggered flushes both acquire
      the lock before modifying the event buffer or file counter
    """

    save_dir: str | None = None
    config: RecordingConfig = field(default_factory=lambda: DEFAULT_CONFIG)

    # State machine
    _state: RecordingState = RecordingState.IDLE
    _event_buffer: EventBuffer = field(default_factory=EventBuffer)

    # File management
    _next_file_index: int = 0  # Next index to probe for available filename
    _files_written: int = 0  # Count of files actually written this session
    _total_events: int = 0

    # Background task
    _flush_task: asyncio.Task | None = field(default=None, repr=False)

    # Browser state
    _scripts_injected: bool = False

    # Concurrency control
    _flush_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

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
        """Save current events to a numbered JSON file.

        Finds the next available filename by incrementing the index until
        an unused filename is found, with a safety limit to prevent infinite loops.

        Returns:
            Path to the saved file, or None if save_dir is not configured or no events.
        """
        if not self.save_dir or not self._event_buffer:
            return None

        os.makedirs(self.save_dir, exist_ok=True)

        # Find the next available filename with safety limit
        attempts = 0
        while attempts < self.config.max_file_counter:
            self._next_file_index += 1
            attempts += 1
            filename = f"{self._next_file_index}.json"
            filepath = os.path.join(self.save_dir, filename)
            if not os.path.exists(filepath):
                break
        else:
            max_attempts = self.config.max_file_counter
            raise RuntimeError(
                f"Failed to find available filename after {max_attempts} attempts"
            )

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
        If events exceed the size threshold, they are saved to disk.

        Thread Safety:
            This method acquires _flush_lock to protect concurrent access to
            the event buffer and file counter from the periodic flush loop
            and navigation-triggered flushes.

        Returns:
            Number of events flushed.
        """
        if self._state != RecordingState.RECORDING:
            return 0

        try:
            cdp_session = await browser_session.get_or_create_cdp_session()
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={"expression": FLUSH_EVENTS_JS, "returnByValue": True},
                session_id=cdp_session.session_id,
            )

            data = json.loads(result.get("result", {}).get("value", "{}"))
            events = data.get("events", [])
            if events:
                async with self._flush_lock:
                    self._event_buffer.add_batch(events)
                    logger.debug(f"Flushed {len(events)} recording events from browser")

                    # Check if we should save to disk (size threshold)
                    if self._event_buffer.should_flush(self.config.flush_size_mb):
                        self.save_events_to_file()

            return len(events)
        except Exception as e:
            logger.warning(f"Failed to flush recording events: {e}")
            return 0

    async def _periodic_flush_loop(self, browser_session: BrowserSession) -> None:
        """Background task that periodically flushes recording events.

        Thread Safety:
            This method acquires _flush_lock when saving events to disk,
            coordinating with navigation-triggered flushes to prevent race
            conditions on the event buffer and file counter.
        """
        while self._state == RecordingState.RECORDING:
            await asyncio.sleep(self.config.flush_interval_seconds)
            if self._state != RecordingState.RECORDING:
                break

            try:
                # Flush events from browser to Python storage (lock is acquired inside)
                await self.flush_events(browser_session)

                # Save to disk if we have any events (periodic save)
                async with self._flush_lock:
                    if self._event_buffer:
                        self.save_events_to_file()
            except Exception as e:
                logger.warning(f"Periodic flush failed: {e}")

    async def start(self, browser_session: BrowserSession) -> str:
        """Start rrweb session recording.

        Will retry up to config.start_max_retries times if rrweb is not loaded yet.
        This handles the case where recording is started before the page fully loads.

        Returns:
            Status message indicating success or failure.
        """
        # Inject scripts if not already done
        if not self._scripts_injected:
            await self.inject_scripts(browser_session)

        # Reset state for new recording session
        self._event_buffer.clear()
        self._state = RecordingState.RECORDING
        self._next_file_index = 0
        self._files_written = 0
        self._total_events = 0

        try:
            cdp_session = await browser_session.get_or_create_cdp_session()

            for attempt in range(self.config.start_max_retries):
                result = await cdp_session.cdp_client.send.Runtime.evaluate(
                    params={"expression": START_RECORDING_JS, "returnByValue": True},
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
                        logger.info(
                            "Recording already active, started periodic flush task"
                        )
                    return "Already recording"

                elif status == "load_failed":
                    self._state = RecordingState.IDLE
                    await self._set_recording_flag(browser_session, False)
                    logger.error(
                        "Unable to start recording: rrweb failed to load from CDN"
                    )
                    return (
                        "Error: Unable to start recording. The rrweb library "
                        "failed to load from CDN. Please check network "
                        "connectivity and try again."
                    )

                elif status == "not_loaded":
                    if attempt < self.config.start_max_retries - 1:
                        logger.debug(
                            f"rrweb not loaded yet, retrying... "
                            f"(attempt {attempt + 1}/{self.config.start_max_retries})"
                        )
                        await asyncio.sleep(self.config.retry_delay_ms / 1000)
                    continue

                else:
                    self._state = RecordingState.IDLE
                    return f"Unknown status: {status}"

            # All retries exhausted
            self._state = RecordingState.IDLE
            await self._set_recording_flag(browser_session, False)
            return (
                "Error: Unable to start recording. rrweb did not load after retries. "
                "Please navigate to a page first and try again."
            )

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
                params={"expression": STOP_RECORDING_JS, "returnByValue": True},
                session_id=cdp_session.session_id,
            )

            current_page_data = json.loads(result.get("result", {}).get("value", "{}"))
            current_page_events = current_page_data.get("events", [])

            # Acquire lock for final event processing to ensure consistency
            async with self._flush_lock:
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
            save_dir_used = self.save_dir

            logger.info(
                f"Recording stopped: {total_events} events saved to "
                f"{total_files} file(s) in {save_dir_used}"
            )

            # Return a concise summary message
            summary = (
                f"Recording stopped. Captured {total_events} events "
                f"in {total_files} file(s)."
            )
            if save_dir_used:
                summary += f" Saved to: {save_dir_used}"

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

        This waits for rrweb to be ready and starts a new recording session.
        Called automatically after navigation when recording is active.
        """
        if self._state != RecordingState.RECORDING:
            return

        try:
            cdp_session = await browser_session.get_or_create_cdp_session()

            for attempt in range(self.config.start_max_retries):
                result = await cdp_session.cdp_client.send.Runtime.evaluate(
                    params={
                        "expression": START_RECORDING_SIMPLE_JS,
                        "returnByValue": True,
                    },
                    session_id=cdp_session.session_id,
                )

                value = result.get("result", {}).get("value", {})
                status = value.get("status") if isinstance(value, dict) else value

                if status == "started":
                    logger.debug("Recording restarted on new page")
                    return

                elif status == "already_recording":
                    logger.debug("Recording already active on new page")
                    return

                elif status == "not_loaded":
                    if attempt < self.config.start_max_retries - 1:
                        await asyncio.sleep(self.config.retry_delay_ms / 1000)
                    continue

            logger.warning("Could not restart recording on new page (rrweb not loaded)")

        except Exception as e:
            logger.warning(f"Failed to restart recording on new page: {e}")

    def reset(self) -> None:
        """Reset the recording session state for reuse."""
        self._event_buffer.clear()
        self._state = RecordingState.IDLE
        self._next_file_index = 0
        self._files_written = 0
        self._total_events = 0
        self._flush_task = None
        # Note: _scripts_injected is NOT reset - scripts persist in browser session
