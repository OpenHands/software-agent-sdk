"""Module-level browser service for pre-initialized browser components."""

import logging
import shutil
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState
    from openhands.tools.browser_use.impl import BrowserToolExecutor

logger = logging.getLogger(__name__)

# Module-level browser service state
_chromium_path: str | None = None
_initialized: bool = False


def is_chromium_available() -> bool:
    """Check if Chromium is available on the system."""
    return (
        shutil.which("chromium") is not None
        or shutil.which("chromium-browser") is not None
    )


def get_chromium_path() -> str | None:
    """Get the path to Chromium executable."""
    return shutil.which("chromium") or shutil.which("chromium-browser")


async def initialize_browser_service() -> bool:
    """Initialize the browser service with Chromium detection.

    Returns:
        True if initialization successful, False otherwise
    """
    global _chromium_path, _initialized

    if _initialized:
        return True

    try:
        # Check for Chromium availability
        if not is_chromium_available():
            logger.warning("Chromium not available - browser tools will be limited")
            _chromium_path = None
        else:
            _chromium_path = get_chromium_path()
            logger.info(
                f"Chromium is available for browser operations at {_chromium_path}"
            )

        _initialized = True
        logger.info("Browser service initialized successfully")
        return True

    except Exception as e:
        logger.error(f"Failed to initialize browser service: {e}")
        return False


async def shutdown_browser_service() -> None:
    """Shutdown the browser service and clean up resources."""
    global _chromium_path, _initialized

    _chromium_path = None
    _initialized = False
    logger.info("Browser service shutdown")


def is_service_available() -> bool:
    """Check if browser service is available and initialized.

    Returns:
        True if available, False otherwise
    """
    return _initialized and _chromium_path is not None


def create_browser_executor(
    conv_state: "ConversationState", **executor_config
) -> "BrowserToolExecutor":
    """Create a browser tool executor using pre-initialized components.

    Args:
        conv_state: Conversation state for the executor
        **executor_config: Additional executor configuration

    Returns:
        Configured BrowserToolExecutor instance

    Raises:
        RuntimeError: If service not initialized or Chromium not available
    """
    if not _initialized:
        raise RuntimeError("Browser service not initialized")

    if _chromium_path is None:
        raise RuntimeError("Chromium not available")

    # Import here to avoid circular dependencies
    from openhands.tools.browser_use.impl import BrowserToolExecutor

    # Create executor with pre-detected Chromium path
    executor_config.setdefault("chromium_path", _chromium_path)

    return BrowserToolExecutor(conv_state=conv_state, **executor_config)
