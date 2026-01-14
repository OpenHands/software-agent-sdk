from browser_use.dom.markdown_extractor import extract_clean_markdown

from openhands.sdk import get_logger
from openhands.tools.browser_use.logging_fix import LogSafeBrowserUseServer


logger = get_logger(__name__)

# rrweb loader script - injected into every page to make rrweb available
# This script loads rrweb from CDN dynamically
# NOTE: Using unpkg instead of jsdelivr because:
# - jsdelivr returns Content-Type: application/node for .cjs files (browser won't execute)
# - jsdelivr's .min.js is ES module format (no global window.rrweb)
# - unpkg returns Content-Type: text/javascript for .cjs files (browser executes it)
RRWEB_CDN_URL = "https://unpkg.com/rrweb@2.0.0-alpha.17/dist/rrweb.umd.cjs"

RRWEB_LOADER_SCRIPT = """
(function() {
    if (window.__rrweb_loaded) return;
    window.__rrweb_loaded = true;

    // Initialize storage for events (per-page, will be flushed to backend)
    window.__rrweb_events = window.__rrweb_events || [];
    window.__rrweb_using_stub = false;
    // Flag to indicate if we should auto-start recording (set by backend)
    window.__rrweb_should_record = window.__rrweb_should_record || false;

    function loadRrweb() {
        var s = document.createElement('script');
        s.src = '""" + RRWEB_CDN_URL + """';
        s.onload = function() {
            window.__rrweb_ready = true;
            window.__rrweb_using_stub = false;
            console.log('[rrweb] Loaded successfully from CDN');
            // Auto-start recording if flag is set (for cross-page continuity)
            if (window.__rrweb_should_record && !window.__rrweb_stopFn) {
                startRecordingInternal();
            }
        };
        s.onerror = function() {
            console.error('[rrweb] Failed to load from CDN, creating minimal stub');
            window.__rrweb_using_stub = true;
            // Create a minimal stub that captures basic events and DOM mutations
            window.rrweb = {
                record: function(opts) {
                    console.log('[rrweb-stub] Recording started');
                    var emitFn = opts.emit;

                    // Emit a meta event (type 4)
                    emitFn({
                        type: 4,
                        data: {
                            href: location.href,
                            width: window.innerWidth,
                            height: window.innerHeight
                        },
                        timestamp: Date.now()
                    });

                    // Emit a full snapshot (type 2) - capture current DOM
                    function serializeNode(node, id) {
                        var obj = {id: id, type: node.nodeType};
                        if (node.nodeType === 1) { // Element
                            obj.tagName = node.tagName.toLowerCase();
                            obj.attributes = {};
                            for (var i = 0; i < node.attributes.length; i++) {
                                obj.attributes[node.attributes[i].name] = node.attributes[i].value;
                            }
                            obj.childNodes = [];
                            var childId = id * 100;
                            for (var j = 0; j < node.childNodes.length && j < 50; j++) {
                                obj.childNodes.push(serializeNode(node.childNodes[j], childId + j));
                            }
                        } else if (node.nodeType === 3) { // Text
                            obj.textContent = node.textContent ? node.textContent.slice(0, 1000) : '';
                        }
                        return obj;
                    }

                    emitFn({
                        type: 2,
                        data: {
                            node: serializeNode(document.documentElement, 1),
                            initialOffset: {top: window.scrollY, left: window.scrollX}
                        },
                        timestamp: Date.now()
                    });

                    // Set up mutation observer for incremental snapshots (type 3)
                    var observer = new MutationObserver(function(mutations) {
                        mutations.forEach(function(mutation) {
                            emitFn({
                                type: 3,
                                data: {
                                    source: 0, // Mutation
                                    texts: [],
                                    attributes: [],
                                    removes: [],
                                    adds: [{parentId: 1, node: {type: 3, textContent: 'mutation'}}]
                                },
                                timestamp: Date.now()
                            });
                        });
                    });
                    observer.observe(document.body || document.documentElement, {
                        childList: true,
                        subtree: true,
                        attributes: true,
                        characterData: true
                    });

                    // Capture scroll events (type 3, source 3)
                    var scrollHandler = function() {
                        emitFn({
                            type: 3,
                            data: {source: 3, x: window.scrollX, y: window.scrollY},
                            timestamp: Date.now()
                        });
                    };
                    window.addEventListener('scroll', scrollHandler);

                    // Capture mouse move events (type 3, source 1)
                    var mouseHandler = function(e) {
                        emitFn({
                            type: 3,
                            data: {source: 1, positions: [{x: e.clientX, y: e.clientY, timeOffset: 0}]},
                            timestamp: Date.now()
                        });
                    };
                    document.addEventListener('mousemove', mouseHandler, {passive: true});

                    // Return a stop function
                    return function() {
                        console.log('[rrweb-stub] Recording stopped');
                        observer.disconnect();
                        window.removeEventListener('scroll', scrollHandler);
                        document.removeEventListener('mousemove', mouseHandler);
                    };
                }
            };
            window.__rrweb_ready = true;
            // Auto-start for stub too
            if (window.__rrweb_should_record && !window.__rrweb_stopFn) {
                startRecordingInternal();
            }
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

# Maximum retries for starting recording
RRWEB_START_MAX_RETRIES = 10
RRWEB_START_RETRY_DELAY_MS = 500


class CustomBrowserUseServer(LogSafeBrowserUseServer):
    """
    Custom BrowserUseServer with a new tool for extracting web
    page's content in markdown.
    """

    # Scripts to inject into every new document (before page scripts run)
    _inject_scripts: list[str] = []
    # Script identifiers returned by CDP (for cleanup if needed)
    _injected_script_ids: list[str] = []

    # Recording state stored on Python side to persist across page navigations
    _recording_events: list[dict] = []
    _is_recording: bool = False
    _recording_using_stub: bool = False

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
        scripts_to_inject = [RRWEB_LOADER_SCRIPT] + self._inject_scripts

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()

            for script in scripts_to_inject:
                result = await cdp_session.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
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

    async def _flush_recording_events(self) -> int:
        """Flush recording events from browser to Python storage.

        This should be called before navigation to preserve events across pages.
        Returns the number of events flushed.
        """
        if not self.browser_session or not self._is_recording:
            return 0

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={
                    "expression": """
                        (function() {
                            var events = window.__rrweb_events || [];
                            var using_stub = !!window.__rrweb_using_stub;
                            // Clear browser-side events after flushing
                            window.__rrweb_events = [];
                            return JSON.stringify({events: events, using_stub: using_stub});
                        })();
                    """,
                    "returnByValue": True,
                },
                session_id=cdp_session.session_id,
            )
            import json
            data = json.loads(result.get("result", {}).get("value", "{}"))
            events = data.get("events", [])
            if events:
                self._recording_events.extend(events)
                if data.get("using_stub"):
                    self._recording_using_stub = True
                logger.debug(f"Flushed {len(events)} recording events from browser")
            return len(events)
        except Exception as e:
            logger.warning(f"Failed to flush recording events: {e}")
            return 0

    async def _set_recording_flag(self, should_record: bool) -> None:
        """Set the recording flag in the browser for auto-start on new pages."""
        if not self.browser_session:
            return

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            await cdp_session.cdp_client.send.Runtime.evaluate(
                params={
                    "expression": f"window.__rrweb_should_record = {str(should_record).lower()};",
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

            # Wait for rrweb to be ready and start recording
            start_recording_js = """
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
                    return {
                        status: 'started',
                        using_stub: !!window.__rrweb_using_stub
                    };
                })();
            """

            # Retry a few times waiting for rrweb to load on new page
            for attempt in range(RRWEB_START_MAX_RETRIES):
                result = await cdp_session.cdp_client.send.Runtime.evaluate(
                    params={"expression": start_recording_js, "returnByValue": True},
                    session_id=cdp_session.session_id,
                )

                value = result.get("result", {}).get("value", {})
                status = value.get("status") if isinstance(value, dict) else value

                if status == "started":
                    if value.get("using_stub"):
                        self._recording_using_stub = True
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

    async def _start_recording(self) -> str:
        """Start rrweb session recording with automatic retry.

        Will retry up to RRWEB_START_MAX_RETRIES times if rrweb is not loaded yet.
        This handles the case where recording is started before the page fully loads.

        Recording persists across page navigations - events are stored on the Python
        side and automatically collected when stop_recording is called.
        """
        import asyncio

        if not self.browser_session:
            return "Error: No browser session active"

        # Reset Python-side storage for new recording session
        self._recording_events = []
        self._is_recording = True
        self._recording_using_stub = False

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()

            # Set flag so new pages auto-start recording
            await self._set_recording_flag(True)

            start_recording_js = """
                (function() {
                    if (window.__rrweb_stopFn) return {status: 'already_recording'};
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
                    return {
                        status: 'started',
                        using_stub: !!window.__rrweb_using_stub,
                        event_count: window.__rrweb_events.length
                    };
                })();
            """

            # Retry loop for starting recording
            for attempt in range(RRWEB_START_MAX_RETRIES):
                result = await cdp_session.cdp_client.send.Runtime.evaluate(
                    params={"expression": start_recording_js, "returnByValue": True},
                    session_id=cdp_session.session_id,
                )

                value = result.get("result", {}).get("value", {})
                status = value.get("status") if isinstance(value, dict) else value

                if status == "started":
                    using_stub = value.get("using_stub", False) if isinstance(value, dict) else False
                    self._recording_using_stub = using_stub
                    if using_stub:
                        logger.warning("Recording started using fallback stub (CDN load failed)")
                        return "Recording started (using fallback recorder - CDN unavailable)"
                    logger.info("Recording started successfully with rrweb")
                    return "Recording started"

                elif status == "already_recording":
                    return "Already recording"

                elif status == "not_loaded":
                    if attempt < RRWEB_START_MAX_RETRIES - 1:
                        logger.debug(
                            f"rrweb not loaded yet, retrying... "
                            f"(attempt {attempt + 1}/{RRWEB_START_MAX_RETRIES})"
                        )
                        await asyncio.sleep(RRWEB_START_RETRY_DELAY_MS / 1000)
                    continue

                else:
                    return f"Unknown status: {status}"

            # All retries exhausted
            self._is_recording = False
            return (
                "rrweb not loaded after retries. "
                "Please navigate to a page first and try again."
            )

        except Exception as e:
            self._is_recording = False
            logger.exception("Error starting recording", exc_info=e)
            return f"Error starting recording: {str(e)}"

    async def _stop_recording(self) -> str:
        """Stop rrweb recording and return events as JSON.

        Returns a JSON object with:
        - events: Array of rrweb events (combined from all pages visited)
        - count: Number of events captured
        - using_stub: Whether the fallback stub was used (CDN unavailable)
        - event_types: Summary of event types captured
        - pages_recorded: Number of pages that were recorded
        """
        import json

        if not self.browser_session:
            return '{"error": "No browser session active"}'

        if not self._is_recording:
            return json.dumps({
                "error": "Not recording",
                "hint": "Call browser_start_recording first"
            })

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()

            # Stop recording on current page and get its events
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={
                    "expression": """
                        (function() {
                            var events = window.__rrweb_events || [];
                            var using_stub = !!window.__rrweb_using_stub;

                            // Stop the recording if active
                            if (window.__rrweb_stopFn) {
                                window.__rrweb_stopFn();
                                window.__rrweb_stopFn = null;
                            }

                            // Clear flags
                            window.__rrweb_should_record = false;
                            window.__rrweb_events = [];

                            return JSON.stringify({
                                events: events,
                                using_stub: using_stub
                            });
                        })();
                    """,
                    "returnByValue": True,
                },
                session_id=cdp_session.session_id,
            )

            current_page_data = json.loads(result.get("result", {}).get("value", "{}"))
            current_page_events = current_page_data.get("events", [])
            if current_page_data.get("using_stub"):
                self._recording_using_stub = True

            # Combine events from Python storage with current page
            all_events = self._recording_events + current_page_events

            # Count event types for summary
            event_types = {}
            type_names = {
                0: 'DomContentLoaded',
                1: 'Load',
                2: 'FullSnapshot',
                3: 'IncrementalSnapshot',
                4: 'Meta',
                5: 'Custom',
                6: 'Plugin'
            }
            for e in all_events:
                type_num = e.get("type", -1)
                type_name = type_names.get(type_num, f'Unknown_{type_num}')
                event_types[type_name] = event_types.get(type_name, 0) + 1

            # Count pages (each FullSnapshot typically represents a new page)
            pages_recorded = event_types.get('FullSnapshot', 0)

            # Reset state
            self._is_recording = False
            await self._set_recording_flag(False)

            # Prepare result
            result_data = {
                "events": all_events,
                "count": len(all_events),
                "using_stub": self._recording_using_stub,
                "event_types": event_types,
                "pages_recorded": pages_recorded
            }

            # Clear Python-side storage
            self._recording_events = []
            self._recording_using_stub = False

            # Log summary
            if self._recording_using_stub:
                logger.warning(f"Recording stopped (fallback stub): {len(all_events)} events from {pages_recorded} page(s)")
            else:
                logger.info(f"Recording stopped: {len(all_events)} events from {pages_recorded} page(s)")
            logger.debug(f"Event types: {event_types}")

            return json.dumps(result_data)

        except Exception as e:
            self._is_recording = False
            logger.exception("Error stopping recording", exc_info=e)
            return json.dumps({"error": str(e)})

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
