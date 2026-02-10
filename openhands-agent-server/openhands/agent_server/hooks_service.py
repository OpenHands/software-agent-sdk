"""Hooks service for OpenHands Agent Server.

This module contains the business logic for loading hooks from the workspace,
keeping the router clean and focused on HTTP concerns.

Hook Sources:
- Project hooks: {workspace}/.openhands/hooks.json
- User hooks: ~/.openhands/hooks.json (future)
"""

from pathlib import Path

from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def load_hooks_from_workspace(project_dir: str | None = None) -> HookConfig | None:
    """Load hooks from the workspace .openhands/hooks.json file.

    This function reads the hooks configuration from the project's
    .openhands/hooks.json file if it exists.

    Args:
        project_dir: Workspace directory path for project hooks.

    Returns:
        HookConfig if hooks.json exists and is valid, None otherwise.
    """
    if not project_dir:
        logger.debug("No project_dir provided, skipping hooks loading")
        return None

    hooks_path = Path(project_dir) / ".openhands" / "hooks.json"

    if not hooks_path.exists():
        logger.debug(f"No hooks.json found at {hooks_path}")
        return None

    try:
        hook_config = HookConfig.load(path=hooks_path)

        if hook_config.is_empty():
            logger.debug(f"hooks.json at {hooks_path} is empty")
            return None

        logger.info(f"Loaded hooks from {hooks_path}")
        return hook_config

    except Exception as e:
        logger.warning(f"Failed to load hooks from {hooks_path}: {e}")
        return None
