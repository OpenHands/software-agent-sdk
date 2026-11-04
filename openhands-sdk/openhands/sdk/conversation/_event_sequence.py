"""Immutable allocation records for event-log sequence numbers."""

import re
from collections.abc import Iterable

from openhands.sdk.io import FileStore
from openhands.sdk.utils.path import posix_path_name


_RESERVATION_NAME = re.compile(r"^\.eventlog-seq-(?P<seq>\d+)\.marker$")
_LEGACY_LENGTH_NAME = re.compile(r"^\.eventlog-len-(?P<length>\d+)\.marker$")


class SequenceJournal:
    """Retain allocation history independently of surviving event files.

    A reservation is immutable and remains after failed publication or lost
    events. Keeping every slot also lets an old writer test its next slot with
    one exists() call without relying on cached mutable file contents.
    """

    def __init__(self, fs: FileStore, directory: str) -> None:
        self._fs = fs
        self._directory = directory

    def _path(self, seq: int) -> str:
        return f"{self._directory}/.eventlog-seq-{seq:05d}.marker"

    def contains(self, seq: int) -> bool:
        return self._fs.exists(self._path(seq))

    def reserve(self, seq: int) -> None:
        """Reserve a slot under the caller's file lock and write guard."""
        self._fs.write(self._path(seq), "")

    @staticmethod
    def next_sequence(paths: Iterable[str]) -> int:
        """Read retained allocations and the available legacy high-water hint."""
        next_seq = 0
        for path in paths:
            name = posix_path_name(path)
            if match := _RESERVATION_NAME.fullmatch(name):
                next_seq = max(next_seq, int(match["seq"]) + 1)
            elif match := _LEGACY_LENGTH_NAME.fullmatch(name):
                next_seq = max(next_seq, int(match["length"]))
        return next_seq
