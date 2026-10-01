from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from uuid import UUID


class Tier(IntEnum):
    """What may be dropped from a stopped runtime; each tier includes the lower."""

    CACHES = 1
    DEPENDENCIES = 2
    RUNTIME = 3


@dataclass(frozen=True, slots=True)
class StoredRuntime:
    """A conversation's storage on this host, limited to what the server owns."""

    id: UUID
    last_active: float
    # The sandbox's own $HOME, if it has one apart from the host's.
    home: Path | None = None
    # Workspaces the server created; never a caller-supplied checkout.
    workspaces: tuple[Path, ...] = ()
