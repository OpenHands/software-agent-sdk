"""Bounded, non-blocking handoff from observed runs to persistence."""

from collections import deque
from threading import Lock

from flight_recorder.models.envelopes import Record


_DROPPABLE_KINDS = {"llm.stream_delta"}


class RecordQueue:
    def __init__(self, maxsize: int = 4096) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be positive")
        self._records: deque[Record] = deque()
        self._maxsize = maxsize
        self._lock = Lock()
        self.dropped = 0

    def put(self, record: Record) -> bool:
        return self.put_many((record,))

    def put_many(self, records: tuple[Record, ...]) -> bool:
        if not records:
            return True
        with self._lock:
            required_space = len(records) - (self._maxsize - len(self._records))
            if required_space <= 0:
                self._records.extend(records)
                return True
            droppable = tuple(
                queued for queued in self._records if queued.kind in _DROPPABLE_KINDS
            )
            if (
                all(record.kind not in _DROPPABLE_KINDS for record in records)
                and len(droppable) >= required_space
            ):
                for queued in droppable[:required_space]:
                    self._records.remove(queued)
                self._records.extend(records)
                self.dropped += required_space
                return True
            self.dropped += len(records)
            return False

    def drain(self, limit: int = 256) -> list[Record]:
        with self._lock:
            return [
                self._records.popleft() for _ in range(min(limit, len(self._records)))
            ]

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)
