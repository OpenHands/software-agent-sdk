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
        with self._lock:
            if len(self._records) < self._maxsize:
                self._records.append(record)
                return True
            if record.kind in _DROPPABLE_KINDS:
                self.dropped += 1
                return False
            for queued in self._records:
                if queued.kind in _DROPPABLE_KINDS:
                    self._records.remove(queued)
                    self._records.append(record)
                    self.dropped += 1
                    return True
            self.dropped += 1
            return False

    def drain(self, limit: int = 256) -> list[Record]:
        with self._lock:
            return [
                self._records.popleft() for _ in range(min(limit, len(self._records)))
            ]

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)
