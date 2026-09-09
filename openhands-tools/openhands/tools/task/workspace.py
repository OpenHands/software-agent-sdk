"""Ownership of workspaces provisioned for individual subagents."""

from collections.abc import Callable

from openhands.sdk.logger import get_logger
from openhands.sdk.workspace import RemoteWorkspace


logger = get_logger(__name__)

SubagentWorkspaceFactory = Callable[[str, str], RemoteWorkspace | None]
"""Create an owned workspace from (child ID, agent type), or None for local work.

Return a fresh workspace for each child. The executor enters it and exits it on
cleanup. Factories that fail during provisioning must clean up their own resources.
"""


def close_workspace(workspace: RemoteWorkspace) -> None:
    """Release a child workspace, including its HTTP connection pool."""
    try:
        workspace.__exit__(None, None, None)
    except Exception:
        logger.warning("Failed to release subagent workspace", exc_info=True)
    finally:
        workspace.reset_client()


def enter_workspace(workspace: RemoteWorkspace) -> None:
    """Enter a newly owned workspace, rolling back partial initialization."""
    try:
        workspace.__enter__()
    except BaseException:
        close_workspace(workspace)
        raise
