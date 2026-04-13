from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol


class ExtensionProtocol(Protocol):
    """Minimal structural protocol for installable extensions.

    Only ``name`` is required.  ``version`` and ``description`` are read
    via ``getattr`` in ``InstallationInfo.from_extension`` so that
    extension types that don't carry those fields (e.g. skills) still
    work without adapter wrappers.

    ``name`` is declared as a read-only property so that both plain
    attributes and ``@property`` accessors satisfy the protocol.
    """

    @property
    def name(self) -> str: ...


class InstallationInterface[T: ExtensionProtocol](ABC):
    """Abstract interface that teaches ``InstallationManager`` how to load ``T``.

    Subclass this and implement ``load_from_dir`` for each concrete
    extension type (e.g. plugins, skills).
    """

    @staticmethod
    @abstractmethod
    def load_from_dir(extension_dir: Path) -> T: ...
