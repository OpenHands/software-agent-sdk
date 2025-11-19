"""OpenHands Workspace - Docker and container-based workspace implementations."""

from typing import TYPE_CHECKING

from .docker import DockerWorkspace
from .remote_api import APIRemoteWorkspace


if TYPE_CHECKING:
    from .docker import DockerDevWorkspace

__all__ = [
    "DockerWorkspace",
    "DockerDevWorkspace",
    "APIRemoteWorkspace",
]


def __getattr__(name: str):
    """Lazy import DockerDevWorkspace to avoid build module imports."""
    if name == "DockerDevWorkspace":
        from .docker import DockerDevWorkspace

        return DockerDevWorkspace
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
