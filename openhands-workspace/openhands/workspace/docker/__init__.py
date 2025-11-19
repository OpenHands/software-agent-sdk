"""Docker workspace implementation."""

from typing import TYPE_CHECKING

from .workspace import DockerWorkspace


if TYPE_CHECKING:
    from .dev_workspace import DockerDevWorkspace

__all__ = ["DockerWorkspace", "DockerDevWorkspace"]


def __getattr__(name: str):
    """Lazy import DockerDevWorkspace to avoid build module imports."""
    if name == "DockerDevWorkspace":
        from .dev_workspace import DockerDevWorkspace

        return DockerDevWorkspace
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
