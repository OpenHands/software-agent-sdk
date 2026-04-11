from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol


class ExtensionProtocol(Protocol):
    """Protocol defining the expected fields needed for an extension.

    These fields are necessary to construct the InstallationInfo object fully, and are
    usually guaranteed by the relevant Anthropic standards.
    """

    name: str
    version: str
    description: str


class InstallationInterface[T: ExtensionProtocol](ABC):
    @staticmethod
    @abstractmethod
    def load_from_dir(extension_dir: Path) -> T: ...
