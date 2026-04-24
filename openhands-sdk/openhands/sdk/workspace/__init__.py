from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


if TYPE_CHECKING:
    from .base import BaseWorkspace
    from .local import LocalWorkspace
    from .models import CommandResult, FileOperationResult, PlatformType, TargetType
    from .remote import AsyncRemoteWorkspace, RemoteWorkspace
    from .workspace import Workspace


__all__ = [
    "AsyncRemoteWorkspace",
    "BaseWorkspace",
    "CommandResult",
    "FileOperationResult",
    "LocalWorkspace",
    "PlatformType",
    "RemoteWorkspace",
    "TargetType",
    "Workspace",
]

_LAZY_IMPORTS = {
    "AsyncRemoteWorkspace": (".remote", "AsyncRemoteWorkspace"),
    "BaseWorkspace": (".base", "BaseWorkspace"),
    "CommandResult": (".models", "CommandResult"),
    "FileOperationResult": (".models", "FileOperationResult"),
    "LocalWorkspace": (".local", "LocalWorkspace"),
    "PlatformType": (".models", "PlatformType"),
    "RemoteWorkspace": (".remote", "RemoteWorkspace"),
    "TargetType": (".models", "TargetType"),
    "Workspace": (".workspace", "Workspace"),
}


def __getattr__(name: str) -> Any:
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
