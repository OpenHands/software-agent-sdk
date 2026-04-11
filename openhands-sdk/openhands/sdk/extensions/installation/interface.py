from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol

from openhands.sdk.extensions.installation.info import InstalledExtensionInfo


class InstallableExtensionProtocol(Protocol):
    name: str


class InstallableExtensionInterface[T: InstallableExtensionProtocol](ABC):
    @staticmethod
    @abstractmethod
    def load_from_dir(extension_dir: Path) -> T: ...

    @staticmethod
    @abstractmethod
    def installation_info(extension: T) -> InstalledExtensionInfo:
        ...
        # TODO: there's no way this signature is all we need
