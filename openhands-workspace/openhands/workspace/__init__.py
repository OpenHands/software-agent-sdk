"""OpenHands Workspace - Docker and container-based workspace implementations."""

from .docker import DockerWorkspace
from .remote_api import APIRemoteWorkspace


__all__ = [
    "DockerWorkspace",
    "APIRemoteWorkspace",
]
