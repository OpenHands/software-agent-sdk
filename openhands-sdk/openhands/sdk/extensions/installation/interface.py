from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol


class ExtensionProtocol(Protocol):
    """Structural protocol for installable extensions.

    All three properties are declared as read-only so that both plain
    Pydantic field attributes and ``@property`` accessors satisfy the
    protocol.
    """

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def description(self) -> str | None: ...


class InstallationInterface[T: ExtensionProtocol](ABC):
    """Abstract interface that teaches ``InstallationManager`` how to load ``T``.

    Subclass this and implement ``load_from_dir`` for each concrete
    extension type (e.g. plugins, skills).
    """

    @staticmethod
    @abstractmethod
    def load_from_dir(extension_dir: Path) -> T: ...
