"""Service which preloads chromium."""

from __future__ import annotations

import os
import threading

from openhands.agent_server.skills_service import (
    discover_profile_skills,
    discover_public_skill_names,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.skills.skill import PUBLIC_SKILLS_PATH_ENV
from openhands.sdk.tool.schema import Action
from openhands.sdk.tool.tool import create_action_type_with_risk
from openhands.sdk.utils.models import get_known_concrete_subclasses


_logger = get_logger(__name__)


def _warm_first_conversation() -> None:
    """Load what the first conversation start would otherwise load inline."""
    try:
        # litellm imports this lazily on the first completion through an
        # OpenAI-compatible client.
        import openai.resources.chat  # noqa: F401

        # Only a local extensions copy: never add a clone to every startup.
        if os.environ.get(PUBLIC_SKILLS_PATH_ENV):
            discover_profile_skills()
            discover_public_skill_names()
    except Exception:
        _logger.debug("First-conversation warm-up failed", exc_info=True)


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
        threading.Thread(
            target=_warm_first_conversation,
            name="first-conversation-warmup",
            daemon=True,
        ).start()
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
