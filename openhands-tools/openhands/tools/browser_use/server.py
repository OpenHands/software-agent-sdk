# Temporarily disable problematic logging configuration BEFORE importing browser_use
import logging


# Store original logging functions and state IMMEDIATELY
_original_logging_disable = logging.disable
_original_logging_basicConfig = logging.basicConfig
_original_root_level = logging.root.level
_original_root_handlers = logging.root.handlers.copy()


def _noop_configure_mcp_server_logging():
    """No-op replacement for _configure_mcp_server_logging to prevent interference."""
    pass


def _noop_ensure_all_loggers_use_stderr():
    """No-op replacement for _ensure_all_loggers_use_stderr to prevent interference."""
    pass


def _noop_logging_disable(level):
    """No-op replacement for logging.disable to prevent disabling logging."""
    pass


def _noop_logging_basicConfig(*args, **kwargs):
    """No-op replacement for logging.basicConfig to prevent reconfiguration."""
    pass


# Replace the problematic functions BEFORE importing the module
logging.disable = _noop_logging_disable
logging.basicConfig = _noop_logging_basicConfig

# Now import the modules - this will trigger problematic code but no-ops prevent issues
import browser_use.mcp.server  # noqa: E402
from browser_use.dom.markdown_extractor import extract_clean_markdown  # noqa: E402


# Replace the functions in the imported module as well
browser_use.mcp.server._configure_mcp_server_logging = (
    _noop_configure_mcp_server_logging
)
browser_use.mcp.server._ensure_all_loggers_use_stderr = (
    _noop_ensure_all_loggers_use_stderr
)

from browser_use.mcp.server import BrowserUseServer  # noqa: E402

from openhands.sdk import get_logger  # noqa: E402


# Get logger before restoring original state
logger = get_logger(__name__)

# Restore original logging functions and state after ALL imports
logging.disable = _original_logging_disable
logging.basicConfig = _original_logging_basicConfig
logging.root.setLevel(_original_root_level)
logging.root.handlers = _original_root_handlers


class CustomBrowserUseServer(BrowserUseServer):
    """
    Custom BrowserUseServer with a new tool for extracting web
    page's content in markdown.
    """

    def __init__(self, session_timeout_minutes: int = 10):
        # Temporarily replace the problematic function during initialization
        original_ensure_stderr = browser_use.mcp.server._ensure_all_loggers_use_stderr
        browser_use.mcp.server._ensure_all_loggers_use_stderr = (
            _noop_ensure_all_loggers_use_stderr
        )

        try:
            super().__init__(session_timeout_minutes)
        finally:
            # Restore the original function
            browser_use.mcp.server._ensure_all_loggers_use_stderr = (
                original_ensure_stderr
            )

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
