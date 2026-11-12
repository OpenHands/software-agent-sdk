"""Service which preloads chromium."""

from __future__ import annotations

from openhands.sdk.logger import get_logger
from openhands.sdk.tool.schema import Action
from openhands.sdk.tool.tool import create_action_type_with_risk
from openhands.sdk.utils.models import get_known_concrete_subclasses


_logger = get_logger(__name__)


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
        try:
            from openhands.tools.browser_use.impl import BrowserToolExecutor

            # Creating an instance here to preload chomium
            BrowserToolExecutor()

            # Pre-creating all these classes prevents processing which costs
            # significant time per tool on the first conversation invocation.
            for action_type in get_known_concrete_subclasses(Action):
                create_action_type_with_risk(action_type)

            _logger.debug(f"Loaded {BrowserToolExecutor}")
            return True
        except Exception:
            _logger.exception("Error preloading chromium")
            return False

    async def stop(self) -> None:
        """Stop the tool preload process."""
        self.running = False

    def is_running(self) -> bool:
        """Check if tool preload is running."""
        return self.running
