from pathlib import Path
from typing import Literal, Self, overload

from openhands.sdk.logger import get_logger
from openhands.sdk.workspace.base import BaseWorkspace
from openhands.sdk.workspace.landlock import LandlockWorkspace, is_landlock_supported
from openhands.sdk.workspace.local import LocalWorkspace
from openhands.sdk.workspace.remote.base import RemoteWorkspace


logger = get_logger(__name__)


class Workspace:
    """Factory entrypoint that returns Local, Landlock, or Remote workspaces.

    Usage:
        - Workspace(working_dir=...) -> LocalWorkspace
        - Workspace(working_dir=..., backend="landlock") -> LandlockWorkspace
        - Workspace(working_dir=..., backend="auto") -> Landlock or Local
        - Workspace(working_dir=..., host="http://...") -> RemoteWorkspace
    """

    @overload
    def __new__(
        cls: type[Self],
        *,
        working_dir: str | Path = "workspace/project",
        backend: Literal["local"] = "local",
    ) -> LocalWorkspace: ...

    @overload
    def __new__(
        cls: type[Self],
        *,
        working_dir: str | Path = "workspace/project",
        backend: Literal["landlock"],
    ) -> LandlockWorkspace: ...

    @overload
    def __new__(
        cls: type[Self],
        *,
        host: str,
        working_dir: str | Path = "workspace/project",
        api_key: str | None = None,
        backend: str = "local",
    ) -> RemoteWorkspace: ...

    @overload
    def __new__(
        cls: type[Self],
        *,
        host: str | None = None,
        working_dir: str | Path = "workspace/project",
        api_key: str | None = None,
        backend: str = "local",
    ) -> BaseWorkspace: ...

    def __new__(
        cls: type[Self],
        *,
        host: str | None = None,
        working_dir: str | Path = "workspace/project",
        api_key: str | None = None,
        backend: str = "local",
    ) -> BaseWorkspace:
        dir_str = str(working_dir)
        if backend == "landlock":
            return LandlockWorkspace(working_dir=dir_str)
        if backend == "auto" and is_landlock_supported():
            return LandlockWorkspace(working_dir=dir_str)
        if host:
            return RemoteWorkspace(
                working_dir=dir_str,
                host=host,
                api_key=api_key,
            )
        return LocalWorkspace(working_dir=dir_str)
