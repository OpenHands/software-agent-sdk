import asyncio

from browser_use.dom.markdown_extractor import extract_clean_markdown

from openhands.sdk import get_logger
from openhands.tools.browser_use.logging_fix import LogSafeBrowserUseServer


logger = get_logger(__name__)

# =============================================================================
# Configuration Constants
# =============================================================================

# Maximum retries for starting recording
RRWEB_START_MAX_RETRIES = 10
RRWEB_START_RETRY_DELAY_MS = 500

# Recording flush configuration
RECORDING_FLUSH_INTERVAL_SECONDS = 5  # Flush every 5 seconds
RECORDING_FLUSH_SIZE_MB = 1  # Flush when events exceed 1 MB

# rrweb CDN URL
# NOTE: Using unpkg instead of jsdelivr because jsdelivr returns
# Content-Type: application/node for .cjs files (browser won't execute)
# and jsdelivr's .min.js is ES module format (no global window.rrweb).
# unpkg returns Content-Type: text/javascript for .cjs files.
RRWEB_CDN_URL = "https://unpkg.com/rrweb@2.0.0-alpha.17/dist/rrweb.umd.cjs"

# =============================================================================
# Injected JavaScript Code
# =============================================================================

# rrweb loader script - injected into every page to make rrweb available
# This script loads rrweb from CDN dynamically and sets up auto-recording
RRWEB_LOADER_JS = (
    """
(function() {
    if (window.__rrweb_loaded) return;
    window.__rrweb_loaded = true;

    // Initialize storage for events (per-page, will be flushed to backend)
    window.__rrweb_events = window.__rrweb_events || [];
    // Flag to indicate if we should auto-start recording (set by backend)
    window.__rrweb_should_record = window.__rrweb_should_record || false;
    // Flag to track if rrweb failed to load
    window.__rrweb_load_failed = false;

    function loadRrweb() {
        var s = document.createElement('script');
        s.src = '"""
    + RRWEB_CDN_URL
    + """';
        s.onload = function() {
            window.__rrweb_ready = true;
            console.log('[rrweb] Loaded successfully from CDN');
            // Auto-start recording if flag is set (for cross-page continuity)
            if (window.__rrweb_should_record && !window.__rrweb_stopFn) {
                startRecordingInternal();
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
# Returns: {status: 'started'|'not_loaded'|'already_recording'}
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
# Returns: {status: 'started'|'not_loaded'|'already_recording'|'load_failed'}
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
# CustomBrowserUseServer Class
# =============================================================================


class CustomBrowserUseServer(LogSafeBrowserUseServer):
    """
    Custom BrowserUseServer with a new tool for extracting web
    page's content in markdown.
    """

    def __init__(self, session_timeout_minutes: int = 10):
        super().__init__(session_timeout_minutes=session_timeout_minutes)
        # Scripts to inject into every new document (before page scripts run)
        self._inject_scripts: list[str] = []
        # Script identifiers returned by CDP (for cleanup if needed)
        self._injected_script_ids: list[str] = []
        # Recording state stored on Python side to persist across page navigations
        self._recording_events: list[dict] = []
        self._is_recording: bool = False
        # Recording flush state
        self._recording_save_dir: str | None = None
        self._recording_file_counter: int = 0
        self._recording_flush_task: asyncio.Task | None = None
        self._recording_total_events: int = 0  # Total events across all files

    def set_inject_scripts(self, scripts: list[str]) -> None:
        """Set scripts to be injected into every new document.

        Args:
            scripts: List of JavaScript code strings to inject.
                     Each script will be evaluated before page scripts run.
        """
        self._inject_scripts = scripts

    async def _inject_scripts_to_session(self) -> None:
        """Inject configured scripts into the browser session using CDP.

        Uses Page.addScriptToEvaluateOnNewDocument to inject scripts that
        will run on every new document before the page's scripts execute.
        Always injects rrweb loader, plus any additional configured scripts.
        """
        if not self.browser_session:
            return

        # Always include rrweb loader, plus any user-configured scripts
        scripts_to_inject = [RRWEB_LOADER_JS] + self._inject_scripts

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            cdp_client = cdp_session.cdp_client

            for script in scripts_to_inject:
                result = await cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
                    params={"source": script, "runImmediately": True},
                    session_id=cdp_session.session_id,
                )
                script_id = result.get("identifier")
                if script_id:
                    self._injected_script_ids.append(script_id)
                    logger.debug(f"Injected script with identifier: {script_id}")

            logger.info(
                f"Injected {len(scripts_to_inject)} script(s) into browser session"
            )
        except Exception as e:
            logger.warning(f"Failed to inject scripts: {e}")

    def _save_events_to_file(self, events: list[dict]) -> str | None:
        """Save events to a numbered JSON file.

        Finds the next available filename by incrementing the counter until
        an unused filename is found. This handles cases where files already
        exist from previous recordings.

        Args:
            events: List of rrweb events to save.

        Returns:
            Path to the saved file, or None if save_dir is not configured.
        """
        import json
        import os

        if not self._recording_save_dir or not events:
            return None

        os.makedirs(self._recording_save_dir, exist_ok=True)

        # Find the next available filename
        while True:
            self._recording_file_counter += 1
            filename = f"{self._recording_file_counter}.json"
            filepath = os.path.join(self._recording_save_dir, filename)
            if not os.path.exists(filepath):
                break

        with open(filepath, "w") as f:
            json.dump(events, f)

        self._recording_total_events += len(events)
        logger.debug(
            f"Saved {len(events)} events to {filename} "
            f"(total: {self._recording_total_events} events in "
            f"{self._recording_file_counter} files)"
        )
        return filepath

    def _get_events_size_bytes(self) -> int:
        """Estimate the size of current events in bytes."""
        import json

        if not self._recording_events:
            return 0
        # Quick estimation using JSON serialization
        return len(json.dumps(self._recording_events))

    async def _flush_recording_events(self) -> int:
        """Flush recording events from browser to Python storage.

        This collects events from the browser and adds them to Python-side storage.
        If events exceed the size threshold, they are saved to disk.
        Returns the number of events flushed.
        """
        if not self.browser_session or not self._is_recording:
            return 0

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={"expression": FLUSH_EVENTS_JS, "returnByValue": True},
                session_id=cdp_session.session_id,
            )
            import json

            data = json.loads(result.get("result", {}).get("value", "{}"))
            events = data.get("events", [])
            if events:
                self._recording_events.extend(events)
                logger.debug(f"Flushed {len(events)} recording events from browser")

                # Check if we should save to disk (size threshold)
                size_bytes = self._get_events_size_bytes()
                if size_bytes > RECORDING_FLUSH_SIZE_MB * 1024 * 1024:
                    self._save_events_to_file(self._recording_events)
                    self._recording_events = []

            return len(events)
        except Exception as e:
            logger.warning(f"Failed to flush recording events: {e}")
            return 0

    async def _periodic_flush_task(self) -> None:
        """Background task that periodically flushes recording events."""
        import asyncio

        while self._is_recording:
            await asyncio.sleep(RECORDING_FLUSH_INTERVAL_SECONDS)
            if not self._is_recording:
                break

            try:
                # Flush events from browser to Python storage
                await self._flush_recording_events()

                # Save to disk if we have any events (periodic save)
                if self._recording_events:
                    self._save_events_to_file(self._recording_events)
                    self._recording_events = []
            except Exception as e:
                logger.warning(f"Periodic flush failed: {e}")

    async def _set_recording_flag(self, should_record: bool) -> None:
        """Set the recording flag in the browser for auto-start on new pages."""
        if not self.browser_session:
            return

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
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

    async def _restart_recording_on_new_page(self) -> None:
        """Restart recording on a new page after navigation.

        This waits for rrweb to be ready and starts a new recording session.
        Called automatically after navigation when recording is active.
        """
        import asyncio

        if not self.browser_session or not self._is_recording:
            return

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()

            # Retry a few times waiting for rrweb to load on new page
            for attempt in range(RRWEB_START_MAX_RETRIES):
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
                    if attempt < RRWEB_START_MAX_RETRIES - 1:
                        await asyncio.sleep(RRWEB_START_RETRY_DELAY_MS / 1000)
                    continue

            logger.warning("Could not restart recording on new page (rrweb not loaded)")

        except Exception as e:
            logger.warning(f"Failed to restart recording on new page: {e}")

    async def _start_recording(self, save_dir: str | None = None) -> str:
        """Start rrweb session recording with automatic retry.

        Will retry up to RRWEB_START_MAX_RETRIES times if rrweb is not loaded yet.
        This handles the case where recording is started before the page fully loads.

        Recording persists across page navigations - events are periodically flushed
        to numbered JSON files (1.json, 2.json, etc.) in the save_dir.

        Args:
            save_dir: Directory to save recording files. If provided, events will be
                periodically saved to numbered JSON files in this directory.
        """
        import asyncio

        if not self.browser_session:
            return "Error: No browser session active"

        # Reset Python-side storage for new recording session
        self._recording_events = []
        self._is_recording = True
        self._recording_save_dir = save_dir
        self._recording_file_counter = 0
        self._recording_total_events = 0

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()

            # Set flag so new pages auto-start recording
            await self._set_recording_flag(True)

            # Retry loop for starting recording
            for attempt in range(RRWEB_START_MAX_RETRIES):
                result = await cdp_session.cdp_client.send.Runtime.evaluate(
                    params={"expression": START_RECORDING_JS, "returnByValue": True},
                    session_id=cdp_session.session_id,
                )

                value = result.get("result", {}).get("value", {})
                status = value.get("status") if isinstance(value, dict) else value

                if status == "started":
                    # Start periodic flush task
                    self._recording_flush_task = asyncio.create_task(
                        self._periodic_flush_task()
                    )
                    logger.info("Recording started successfully with rrweb")
                    return "Recording started"

                elif status == "already_recording":
                    return "Already recording"

                elif status == "load_failed":
                    # rrweb CDN load failed - inform agent and don't retry
                    self._is_recording = False
                    await self._set_recording_flag(False)
                    logger.error(
                        "Unable to start recording: rrweb failed to load from CDN"
                    )
                    return (
                        "Error: Unable to start recording. The rrweb library "
                        "failed to load from CDN. Please check network "
                        "connectivity and try again."
                    )

                elif status == "not_loaded":
                    if attempt < RRWEB_START_MAX_RETRIES - 1:
                        logger.debug(
                            f"rrweb not loaded yet, retrying... "
                            f"(attempt {attempt + 1}/{RRWEB_START_MAX_RETRIES})"
                        )
                        await asyncio.sleep(RRWEB_START_RETRY_DELAY_MS / 1000)
                    continue

                else:
                    self._is_recording = False
                    return f"Unknown status: {status}"

            # All retries exhausted
            self._is_recording = False
            await self._set_recording_flag(False)
            return (
                "Error: Unable to start recording. rrweb did not load after retries. "
                "Please navigate to a page first and try again."
            )

        except Exception as e:
            self._is_recording = False
            logger.exception("Error starting recording", exc_info=e)
            return f"Error starting recording: {str(e)}"

    async def _stop_recording(self, save_dir: str | None = None) -> str:  # noqa: ARG002
        """Stop rrweb recording and save remaining events.

        Stops the periodic flush task, collects any remaining events from the
        browser, and saves them to a final numbered JSON file.

        Note: The save_dir parameter is ignored - the directory configured at
        start_recording time is used. This parameter is kept for API compatibility.

        Returns:
            A summary message with the save directory and file count.
        """
        import json

        if not self.browser_session:
            return "Error: No browser session active"

        if not self._is_recording:
            return "Error: Not recording. Call browser_start_recording first."

        try:
            # Stop the periodic flush task first
            self._is_recording = False
            if self._recording_flush_task:
                self._recording_flush_task.cancel()
                try:
                    await self._recording_flush_task
                except (asyncio.CancelledError, Exception):
                    pass  # Task was cancelled, this is expected
                self._recording_flush_task = None

            cdp_session = await self.browser_session.get_or_create_cdp_session()

            # Stop recording on current page and get remaining events
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={"expression": STOP_RECORDING_JS, "returnByValue": True},
                session_id=cdp_session.session_id,
            )

            current_page_data = json.loads(result.get("result", {}).get("value", "{}"))
            current_page_events = current_page_data.get("events", [])

            # Add current page events to in-memory storage
            if current_page_events:
                self._recording_events.extend(current_page_events)

            # Save any remaining events to a final file
            if self._recording_events:
                self._save_events_to_file(self._recording_events)

            await self._set_recording_flag(False)

            # Calculate totals
            total_events = self._recording_total_events
            total_files = self._recording_file_counter
            save_dir_used = self._recording_save_dir

            # Clear Python-side storage
            self._recording_events = []
            self._recording_save_dir = None
            self._recording_file_counter = 0
            self._recording_total_events = 0

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
            self._is_recording = False
            if self._recording_flush_task:
                self._recording_flush_task.cancel()
                self._recording_flush_task = None
            logger.exception("Error stopping recording", exc_info=e)
            return f"Error stopping recording: {str(e)}"

    async def _get_storage(self) -> str:
        """Get browser storage (cookies, local storage, session storage)."""
        import json

        if not self.browser_session:
            return "Error: No browser session active"

        try:
            # Use the private method from BrowserSession to get storage state
            # This returns a dict with 'cookies' and 'origins'
            # (localStorage/sessionStorage)
            storage_state = await self.browser_session._cdp_get_storage_state()
            return json.dumps(storage_state, indent=2)
        except Exception as e:
            logger.exception("Error getting storage state", exc_info=e)
            return f"Error getting storage state: {str(e)}"

    async def _set_storage(self, storage_state: dict) -> str:
        """Set browser storage (cookies, local storage, session storage)."""
        if not self.browser_session:
            return "Error: No browser session active"

        try:
            # 1. Set cookies
            cookies = storage_state.get("cookies", [])
            if cookies:
                await self.browser_session._cdp_set_cookies(cookies)

            # 2. Set local/session storage
            origins = storage_state.get("origins", [])
            if origins:
                cdp_session = await self.browser_session.get_or_create_cdp_session()

                # Enable DOMStorage
                await cdp_session.cdp_client.send.DOMStorage.enable(
                    session_id=cdp_session.session_id
                )

                try:
                    for origin_data in origins:
                        origin = origin_data.get("origin")
                        if not origin:
                            continue

                        dom_storage = cdp_session.cdp_client.send.DOMStorage

                        # Set localStorage
                        for item in origin_data.get("localStorage", []):
                            key = item.get("key") or item.get("name")
                            if not key:
                                continue
                            await dom_storage.setDOMStorageItem(
                                params={
                                    "storageId": {
                                        "securityOrigin": origin,
                                        "isLocalStorage": True,
                                    },
                                    "key": key,
                                    "value": item["value"],
                                },
                                session_id=cdp_session.session_id,
                            )

                        # Set sessionStorage
                        for item in origin_data.get("sessionStorage", []):
                            key = item.get("key") or item.get("name")
                            if not key:
                                continue
                            await dom_storage.setDOMStorageItem(
                                params={
                                    "storageId": {
                                        "securityOrigin": origin,
                                        "isLocalStorage": False,
                                    },
                                    "key": key,
                                    "value": item["value"],
                                },
                                session_id=cdp_session.session_id,
                            )
                finally:
                    # Disable DOMStorage
                    await cdp_session.cdp_client.send.DOMStorage.disable(
                        session_id=cdp_session.session_id
                    )

            return "Storage set successfully"
        except Exception as e:
            logger.exception("Error setting storage state", exc_info=e)
            return f"Error setting storage state: {str(e)}"

    async def _get_content(self, extract_links=False, start_from_char: int = 0) -> str:
        MAX_CHAR_LIMIT = 30000

        if not self.browser_session:
            return "Error: No browser session active"

        # Extract clean markdown using the new method
        try:
            content, content_stats = await extract_clean_markdown(
                browser_session=self.browser_session, extract_links=extract_links
            )
        except Exception as e:
            logger.exception(
                "Error extracting clean markdown", exc_info=e, stack_info=True
            )
            return f"Could not extract clean markdown: {type(e).__name__}"

        # Original content length for processing
        final_filtered_length = content_stats["final_filtered_chars"]

        if start_from_char > 0:
            if start_from_char >= len(content):
                return f"start_from_char ({start_from_char}) exceeds content length ({len(content)}). Content has {final_filtered_length} characters after filtering."  # noqa: E501

            content = content[start_from_char:]
            content_stats["started_from_char"] = start_from_char

        # Smart truncation with context preservation
        truncated = False
        if len(content) > MAX_CHAR_LIMIT:
            # Try to truncate at a natural break point (paragraph, sentence)
            truncate_at = MAX_CHAR_LIMIT

            # Look for paragraph break within last 500 chars of limit
            paragraph_break = content.rfind(
                "\n\n", MAX_CHAR_LIMIT - 500, MAX_CHAR_LIMIT
            )
            if paragraph_break > 0:
                truncate_at = paragraph_break
            else:
                # Look for sentence break within last 200 chars of limit
                sentence_break = content.rfind(
                    ".", MAX_CHAR_LIMIT - 200, MAX_CHAR_LIMIT
                )
                if sentence_break > 0:
                    truncate_at = sentence_break + 1

            content = content[:truncate_at]
            truncated = True
            next_start = (start_from_char or 0) + truncate_at
            content_stats["truncated_at_char"] = truncate_at
            content_stats["next_start_char"] = next_start

        # Add content statistics to the result
        original_html_length = content_stats["original_html_chars"]
        initial_markdown_length = content_stats["initial_markdown_chars"]
        chars_filtered = content_stats["filtered_chars_removed"]

        stats_summary = (
            f"Content processed: {original_html_length:,}"
            + f" HTML chars → {initial_markdown_length:,}"
            + f" initial markdown → {final_filtered_length:,} filtered markdown"
        )
        if start_from_char > 0:
            stats_summary += f" (started from char {start_from_char:,})"
        if truncated:
            stats_summary += f" → {len(content):,} final chars (truncated, use start_from_char={content_stats['next_start_char']} to continue)"  # noqa: E501
        elif chars_filtered > 0:
            stats_summary += f" (filtered {chars_filtered:,} chars of noise)"

        prompt = f"""<content_stats>
{stats_summary}
</content_stats>

<webpage_content>
{content}
</webpage_content>"""
        current_url = await self.browser_session.get_current_page_url()

        return f"""<url>
{current_url}
</url>
<content>
{prompt}
</content>"""
