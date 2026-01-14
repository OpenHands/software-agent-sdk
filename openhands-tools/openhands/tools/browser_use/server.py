from browser_use.dom.markdown_extractor import extract_clean_markdown

from openhands.sdk import get_logger
from openhands.tools.browser_use.logging_fix import LogSafeBrowserUseServer


logger = get_logger(__name__)

# rrweb loader script - injected into every page to make rrweb available
# This script loads rrweb from CDN dynamically
RRWEB_CDN_URL = "https://cdn.jsdelivr.net/npm/rrweb@2.0.0-alpha.17/dist/rrweb.umd.cjs"

RRWEB_LOADER_SCRIPT = """
(function() {
    if (window.__rrweb_loaded) return;
    window.__rrweb_loaded = true;

    // Create a simple fallback in case CDN fails
    window.__rrweb_events = window.__rrweb_events || [];

    function loadRrweb() {
        var s = document.createElement('script');
        s.src = '""" + RRWEB_CDN_URL + """';
        s.onload = function() {
            window.__rrweb_ready = true;
            console.log('[rrweb] Loaded successfully from CDN');
        };
        s.onerror = function() {
            console.error('[rrweb] Failed to load from CDN, creating minimal stub');
            // Create a minimal stub that just captures basic events
            window.rrweb = {
                record: function(opts) {
                    console.log('[rrweb-stub] Recording started');
                    var emitFn = opts.emit;
                    // Emit a meta event
                    emitFn({type: 4, data: {href: location.href, width: window.innerWidth, height: window.innerHeight}, timestamp: Date.now()});
                    // Emit a full snapshot stub
                    emitFn({type: 2, data: {node: {type: 0, childNodes: []}}, timestamp: Date.now()});
                    // Return a stop function
                    return function() { console.log('[rrweb-stub] Recording stopped'); };
                }
            };
            window.__rrweb_ready = true;
        };
        (document.head || document.documentElement).appendChild(s);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', loadRrweb);
    } else {
        loadRrweb();
    }
})();
"""


class CustomBrowserUseServer(LogSafeBrowserUseServer):
    """
    Custom BrowserUseServer with a new tool for extracting web
    page's content in markdown.
    """

    # Scripts to inject into every new document (before page scripts run)
    _inject_scripts: list[str] = []
    # Script identifiers returned by CDP (for cleanup if needed)
    _injected_script_ids: list[str] = []

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

    async def _start_recording(self) -> str:
        """Start rrweb session recording."""
        if not self.browser_session:
            return "Error: No browser session active"

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={
                    "expression": """
                        (function() {
                            if (window.__rrweb_stopFn) return 'Already recording';
                            // rrweb UMD module exports to window.rrweb (not rrwebRecord)
                            var recordFn = (typeof rrweb !== 'undefined' && rrweb.record) ||
                                           (typeof rrwebRecord !== 'undefined' && rrwebRecord.record);
                            if (!recordFn) return 'rrweb not loaded yet - try again after page loads';
                            window.__rrweb_events = [];
                            window.__rrweb_stopFn = recordFn({
                                emit: function(event) {
                                    window.__rrweb_events.push(event);
                                }
                            });
                            return 'Recording started';
                        })();
                    """,
                    "returnByValue": True,
                },
                session_id=cdp_session.session_id,
            )
            return result.get("result", {}).get("value", "Unknown error")
        except Exception as e:
            logger.exception("Error starting recording", exc_info=e)
            return f"Error starting recording: {str(e)}"

    async def _stop_recording(self) -> str:
        """Stop rrweb recording and return events as JSON."""
        if not self.browser_session:
            return '{"error": "No browser session active"}'

        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={
                    "expression": """
                        (function() {
                            if (!window.__rrweb_stopFn) return JSON.stringify({error: 'Not recording'});
                            window.__rrweb_stopFn();
                            var events = window.__rrweb_events || [];
                            window.__rrweb_stopFn = null;
                            window.__rrweb_events = [];
                            return JSON.stringify({events: events, count: events.length});
                        })();
                    """,
                    "returnByValue": True,
                },
                session_id=cdp_session.session_id,
            )
            return result.get("result", {}).get("value", "{}")
        except Exception as e:
            logger.exception("Error stopping recording", exc_info=e)
            return '{"error": "' + str(e) + '"}'

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
