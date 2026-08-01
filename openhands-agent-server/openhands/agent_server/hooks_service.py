"""Hooks service for OpenHands Agent Server.

This module contains the business logic for loading hooks from the workspace,
keeping the router clean and focused on HTTP concerns.

Hook Sources:
- Project hooks: {workspace}/<dir>/hooks.json for each dir in HOOK_CONFIG_DIRS
- User hooks: ~/<dir>/hooks.json (future)
"""

from openhands.sdk.hooks import HOOK_CONFIG_DIRS, HookConfig, find_hooks_file
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def load_hooks_from_workspace(project_dir: str | None = None) -> HookConfig | None:
    """Load hooks from the workspace's hooks.json file.

    Looks for `hooks.json` under each of `HOOK_CONFIG_DIRS` inside the project
    directory and reads the first one that exists.

    Args:
        project_dir: Workspace directory path for project hooks.

    Returns:
        HookConfig if hooks.json exists and is valid, None otherwise.
    """
    if not project_dir:
        logger.debug("No project_dir provided, skipping hooks loading")
        return None

    hooks_path = find_hooks_file(project_dir)

    if hooks_path is None:
        logger.debug(
            f"No hooks.json found in {project_dir} "
            f"(searched {', '.join(HOOK_CONFIG_DIRS)})"
        )
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
