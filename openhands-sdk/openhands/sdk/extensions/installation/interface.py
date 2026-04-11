from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol


class ExtensionProtocol(Protocol):
    """Structural protocol for installable extensions.

    Any object with these three attributes can be managed by the
    installation system.  The fields map directly to
    ``InstallationInfo.name``, ``.version``, and ``.description``.
    """

    name: str
    version: str
    description: str


class InstallationInterface[T: ExtensionProtocol](ABC):
    """Abstract interface that teaches ``InstallationManager`` how to load ``T``.

    Subclass this and implement ``load_from_dir`` for each concrete
    extension type (e.g. plugins, skills).
    """

    @staticmethod
    @abstractmethod
    def load_from_dir(extension_dir: Path) -> T: ...
