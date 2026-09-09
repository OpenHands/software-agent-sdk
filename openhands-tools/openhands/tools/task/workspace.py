"""Ownership of workspaces provisioned for individual subagents."""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.logger import get_logger
from openhands.sdk.workspace import RemoteWorkspace


logger = get_logger(__name__)


class SubagentWorkspaceReference(BaseModel):
    """Durable provider identity, without credentials or a live workspace object."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    working_dir: str = Field(min_length=1)


@dataclass(frozen=True)
class SubagentWorkspace:
    """A newly provisioned workspace and the identity needed to reconnect to it."""

    workspace: RemoteWorkspace
    reference: SubagentWorkspaceReference


SubagentWorkspaceFactory = Callable[
    [str, str], RemoteWorkspace | SubagentWorkspace | None
]
"""Create an owned workspace from (child ID, agent type), or None for local work.

Return a fresh workspace for each child. The executor enters it and exits it on
cleanup. Factories that fail during provisioning must clean up their own resources.
Return SubagentWorkspace with a durable reference to support process restarts.
"""

SubagentWorkspaceResolver = Callable[
    [SubagentWorkspaceReference], RemoteWorkspace | None
]
"""Reconnect to the referenced workspace; never provision a replacement.

Retrieve current endpoints and credentials from the provider using the durable
identity. Return None (or raise) when that workspace no longer exists.
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
