# state.py
import json
import operator
from collections.abc import Iterator
from typing import SupportsIndex, overload

from pydantic import ValidationError

from openhands.sdk.conversation.events_list_base import EventsListBase
from openhands.sdk.conversation.persistence_const import (
    EVENT_FILE_PATTERN,
    EVENT_NAME_RE,
    EVENTS_DIR,
)
from openhands.sdk.event import Event, EventID, UnknownEvent
from openhands.sdk.io import FileStore
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

LOCK_FILE_NAME = ".eventlog.lock"
LOCK_TIMEOUT_SECONDS = 30


def _deserialize_event(txt: str, path: str) -> Event:
    """Deserialize an event, falling back to ``UnknownEvent`` when its tool
    schema is no longer registered."""
    try:
        return Event.model_validate_json(txt)
    except ValidationError:
        data = json.loads(txt)
        if not isinstance(data, dict):
            raise
        kind = str(data.get("kind") or "<unknown>")
        logger.warning(
            "Unloadable event at %s (kind=%s); using UnknownEvent.", path, kind
        )
        return UnknownEvent(
            id=data["id"],
            timestamp=data["timestamp"],
            source=data["source"],
            original_kind=kind,
            original_data=data,
            tool_name=data.get("tool_name"),
            tool_call_id=data.get("tool_call_id"),
            llm_response_id=data.get("llm_response_id"),
            action_id=data.get("action_id"),
        )


class EventLog(EventsListBase):
    """Persistent event log with locking for concurrent writes.

    This class provides thread-safe and process-safe event storage using
    the FileStore's locking mechanism. Events are persisted to disk and
    can be accessed by index or event ID.

    Note:
        For LocalFileStore, file locking via flock() does NOT work reliably
        on NFS mounts or network filesystems. Users deploying with shared
        storage should use alternative coordination mechanisms.
    """

    _fs: FileStore
    _dir: str
    _length: int
    _lock_path: str

    def __init__(self, fs: FileStore, dir_path: str = EVENTS_DIR) -> None:
        self._fs = fs
        self._dir = dir_path
        self._id_to_idx: dict[EventID, int] = {}
        self._idx_to_id: dict[int, EventID] = {}
        self._lock_path = f"{dir_path}/{LOCK_FILE_NAME}"
        self._length = self._scan_and_build_index()

    def get_index(self, event_id: EventID) -> int:
        """Return the integer index for a given event_id."""
        try:
            return self._id_to_idx[event_id]
        except KeyError:
            raise KeyError(f"Unknown event_id: {event_id}")

    def get_id(self, idx: int) -> EventID:
        """Return the event_id for a given index."""
        if idx < 0:
            idx += self._length
        if idx < 0 or idx >= self._length:
            raise IndexError("Event index out of range")
        return self._idx_to_id[idx]

    @overload
    def __getitem__(self, idx: int) -> Event: ...

    @overload
    def __getitem__(self, idx: slice) -> list[Event]: ...

    def __getitem__(self, idx: SupportsIndex | slice) -> Event | list[Event]:
        if isinstance(idx, slice):
            start, stop, step = idx.indices(self._length)
            return [self._get_single_item(i) for i in range(start, stop, step)]
        return self._get_single_item(idx)

    def _get_single_item(self, idx: SupportsIndex) -> Event:
        i = operator.index(idx)
        if i < 0:
            i += self._length
        if i < 0 or i >= self._length:
            raise IndexError("Event index out of range")
        try:
            path = self._path(i)
        except KeyError:
            # In-memory index is stale (e.g., external file modifications
            # or concurrent writes).  Rebuild from disk and retry once.
            logger.warning("Stale EventLog index at %d; rebuilding from disk.", i)
            self._length = self._scan_and_build_index()
            if i >= self._length:
                raise IndexError("Event index out of range")
            path = self._path(i)
        txt = self._fs.read(path)
        if not txt:
            raise FileNotFoundError(f"Missing event file: {path}")
        return _deserialize_event(txt, path)

    def __iter__(self) -> Iterator[Event]:
        for i in range(self._length):
            path = self._path(i)
            txt = self._fs.read(path)
            if not txt:
                continue
            evt = _deserialize_event(txt, path)
            evt_id = evt.id
            if i not in self._idx_to_id:
                self._idx_to_id[i] = evt_id
                self._id_to_idx.setdefault(evt_id, i)
            yield evt

    def append(self, event: Event) -> None:
        """Append an event with locking for thread/process safety.

        Raises:
            TimeoutError: If the lock cannot be acquired within LOCK_TIMEOUT_SECONDS.
            ValueError: If an event with the same ID already exists.
        """
        evt_id = event.id

        try:
            with self._fs.lock(self._lock_path, timeout=LOCK_TIMEOUT_SECONDS):
                # Sync with disk in case another process wrote while we waited
                disk_length = self._count_events_on_disk()
                if disk_length > self._length:
                    self._sync_from_disk(disk_length)

                if evt_id in self._id_to_idx:
                    existing_idx = self._id_to_idx[evt_id]
                    raise ValueError(
                        f"Event with ID '{evt_id}' already exists at index "
                        f"{existing_idx}"
                    )

                target_path = self._path(self._length, event_id=evt_id)
                self._fs.write(target_path, event.model_dump_json(exclude_none=True))
                self._idx_to_id[self._length] = evt_id
                self._id_to_idx[evt_id] = self._length
                self._length += 1
        except TimeoutError:
            logger.error(
                f"Failed to acquire EventLog lock within {LOCK_TIMEOUT_SECONDS}s "
                f"for event {evt_id}"
            )
            raise

    def _count_events_on_disk(self) -> int:
        """Count event files on disk."""
        try:
            paths = self._fs.list(self._dir)
        except FileNotFoundError:
            # Directory doesn't exist yet - expected for new event logs
            return 0
        except Exception as e:
            logger.warning("Error listing event directory %s: %s", self._dir, e)
            return 0
        return sum(
            1
            for p in paths
            if p.rsplit("/", 1)[-1].startswith("event-") and p.endswith(".json")
        )

    def _sync_from_disk(self, disk_length: int) -> None:
        """Sync state for events written by other processes.

        Preserves existing index mappings and only scans new events.
        """
        # Preserve existing mappings
        existing_idx_to_id = dict(self._idx_to_id)

        # Re-scan to pick up new events
        scanned_length = self._scan_and_build_index()

        # Restore any mappings that were lost (e.g., for non-UUID event IDs)
        for idx, evt_id in existing_idx_to_id.items():
            if idx not in self._idx_to_id:
                self._idx_to_id[idx] = evt_id
            if evt_id not in self._id_to_idx:
                self._id_to_idx[evt_id] = idx

        # Use the higher of scanned length or disk_length
        self._length = max(scanned_length, disk_length)

    def __len__(self) -> int:
        return self._length

    def _path(self, idx: int, *, event_id: EventID | None = None) -> str:
        return f"{self._dir}/{
            EVENT_FILE_PATTERN.format(
                idx=idx, event_id=event_id or self._idx_to_id[idx]
            )
        }"

    def _scan_and_build_index(self) -> int:
        try:
            paths = self._fs.list(self._dir)
        except Exception:
            self._id_to_idx.clear()
            self._idx_to_id.clear()
            return 0

        by_idx: dict[int, EventID] = {}
        for p in paths:
            name = p.rsplit("/", 1)[-1]
            m = EVENT_NAME_RE.match(name)
            if m:
                idx = int(m.group("idx"))
                evt_id = m.group("event_id")
                by_idx[idx] = evt_id
            else:
                logger.warning(f"Unrecognized event file name: {name}")

        if not by_idx:
            self._id_to_idx.clear()
            self._idx_to_id.clear()
            return 0

        n = 0
        while True:
            if n not in by_idx:
                if any(i > n for i in by_idx.keys()):
                    logger.warning(
                        "Event index gap detected: "
                        f"expect next index {n} but got {sorted(by_idx.keys())}"
                    )
                break
            n += 1

        self._id_to_idx.clear()
        self._idx_to_id.clear()
        for i in range(n):
            evt_id = by_idx[i]
            self._idx_to_id[i] = evt_id
            if evt_id in self._id_to_idx:
                logger.warning(
                    f"Duplicate event ID '{evt_id}' found during scan. "
                    f"Keeping first occurrence at index {self._id_to_idx[evt_id]}, "
                    f"ignoring duplicate at index {i}"
                )
            else:
                self._id_to_idx[evt_id] = i
        return n
