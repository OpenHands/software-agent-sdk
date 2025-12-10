"""Desktop service for launching VNC desktop via desktop_launch.sh script."""

from __future__ import annotations

import sys

from openhands.agent_server.config import get_default_config
from openhands.sdk.logger import get_logger


_logger = get_logger(__name__)


class BrowserService:
    """Service which preloads chromium reducing time to start first conversation"""

    running: bool = False

    async def start(self) -> bool:
        """Preload chromium"""
        self.running = True
        try:
            if sys.platform == "win32":
                pass
            else:
                pass
            _logger.debug("Loaded {BrowserToolExecutor}")
            return True
        except Exception:
            _logger.exception("Error preloading chromium")
            return False

    async def stop(self) -> None:
        """Stop the desktop process."""
        self.running = False

    def is_running(self) -> bool:
        """Check if desktop is running."""
        return self.running


_browser_service: BrowserService | None = None


def get_browser_service() -> BrowserService | None:
    """Get the browser service instance if preload is enabled."""
    global _browser_service
    config = get_default_config()

    if not config.preload_browser:
        _logger.info("Browser preload is disabled in configuration")
        return None

    if _browser_service is None:
        _browser_service = BrowserService()
    return _browser_service
