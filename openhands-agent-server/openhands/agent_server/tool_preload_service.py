"""Service which preloads chromium."""

from __future__ import annotations

import shutil
import sys

from openhands.agent_server.config import get_default_config
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.schema import Action
from openhands.sdk.tool.tool import create_action_type_with_risk
from openhands.sdk.utils.models import get_known_concrete_subclasses


_logger = get_logger(__name__)


def _check_chromium_available() -> bool:
    """Quick check if any chromium/chrome binary is available in PATH."""
    for binary in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        if shutil.which(binary):
            return True
    return False


class ToolPreloadService:
    """Service which preloads tools / chromium reducing time to
    start first conversation"""

    running: bool = False

    async def start(self) -> bool:
        """Preload tools"""

        # Skip if already running
        if self.running:
            return True

        self.running = True

        # Always preload action types regardless of chromium availability
        try:
            for action_type in get_known_concrete_subclasses(Action):
                create_action_type_with_risk(action_type)
        except Exception:
            _logger.exception("Error preloading action types")

        # Only attempt to preload browser tools if chromium is available
        # This prevents noisy errors in evaluation environments without browsers
        if not _check_chromium_available():
            _logger.warning(
                "Chromium not found in PATH - skipping browser tool preload. "
                "Browser tools will be initialized on-demand if chromium "
                "becomes available."
            )
            return True

        try:
            if sys.platform == "win32":
                from openhands.tools.browser_use.impl_windows import (
                    WindowsBrowserToolExecutor as BrowserToolExecutor,
                )
            else:
                from openhands.tools.browser_use.impl import BrowserToolExecutor

            # Creating an instance here to preload chromium
            BrowserToolExecutor()

            _logger.debug(f"Loaded {BrowserToolExecutor}")
            return True
        except Exception:
            _logger.warning(
                "Failed to preload browser tools - they will be initialized on-demand",
                exc_info=True,
            )
            return True  # Return True since non-browser tools were loaded

    async def stop(self) -> None:
        """Stop the tool preload process."""
        self.running = False

    def is_running(self) -> bool:
        """Check if tool preload is running."""
        return self.running


_tool_preload_service: ToolPreloadService | None = None


def get_tool_preload_service() -> ToolPreloadService | None:
    """Get the tool preload service instance if preload is enabled."""
    global _tool_preload_service
    config = get_default_config()

    if not config.preload_tools:
        _logger.info("Tool preload is disabled in configuration")
        return None

    if _tool_preload_service is None:
        _tool_preload_service = ToolPreloadService()
    return _tool_preload_service
