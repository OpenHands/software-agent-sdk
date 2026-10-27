# state.py
import bisect
import operator
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from typing import Final, SupportsIndex, overload

from openhands.sdk.conversation.events_list_base import EventsListBase
from openhands.sdk.conversation.persistence_const import (
    EVENT_FILE_PATTERN,
    EVENT_NAME_RE,
    EVENTS_DIR,
)
from openhands.sdk.event import Event, EventID
from openhands.sdk.event.types import ROOT_PARENT_ID
from openhands.sdk.io import FileStore
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.path import posix_path_name


logger = get_logger(__name__)

LOCK_FILE_NAME = ".eventlog.lock"
LOCK_TIMEOUT_SECONDS = 30

# Sidecar marker naming the log's event count, so a writer can check it with one
# ``exists()``. The count lives in the name because ``FileStore.read`` is cached.
LENGTH_MARKER_PATTERN: Final[str] = ".eventlog-len-{length}.marker"

# Holes listed by name in the lost-files warning; the rest are only counted.
MAX_REPORTED_HOLES: Final[int] = 5

# ROOT_PARENT_ID now lives in event.types (single source of truth); it is used
# below in _effective_parent_id and re-exported here so existing
# ``from event_store import ROOT_PARENT_ID`` importers keep working.


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
    _write_guard: Callable[[], AbstractContextManager[None]] | None

    def __init__(self, fs: FileStore, dir_path: str = EVENTS_DIR) -> None:
        self._fs = fs
        self._dir = dir_path
        self._id_to_idx: dict[EventID, int] = {}
        self._idx_to_id: dict[int, EventID] = {}
        # On-disk index per log index, recorded only where the two differ (behind
        # a lost event file). The on-disk index names the file and is the
        # event's durable seq; new files are allocated after the highest one.
        self._disk_idx: dict[int, int] = {}
        self._next_disk_idx = 0
        self._reported_lost = 0
        self._reported_missing: set[EventID] = set()
        self._event_cache: dict[int, Event] = {}
        self._lock_path = f"{dir_path}/{LOCK_FILE_NAME}"
        self._write_guard = None
        self._length = self._scan_and_build_index()

    def set_write_guard(
        self,
        write_guard: Callable[[], AbstractContextManager[None]] | None,
    ) -> None:
        self._write_guard = write_guard

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

    def get_seq(self, idx: int) -> int:
        """Return the durable sequence number of the event at ``idx``.

        The seq is the event's on-disk index (the ``{idx:05d}`` filename
        component). It equals ``idx`` unless an earlier event file was lost, and
        unlike ``idx`` it never changes across a reload, so it is the value to
        hand out as a resume cursor.
        """
        if idx < 0:
            idx += self._length
        if idx < 0 or idx >= self._length:
            raise IndexError("Event index out of range")
        return self._disk_idx.get(idx, idx)

    def index_after_seq(self, seq: int) -> int:
        """Return the index of the first event whose seq is above ``seq``.

        Returns ``len(self)`` when there is none. Seqs grow with the index, so
        this is a binary search.
        """
        return bisect.bisect_right(range(self._length), seq, key=self.get_seq)

    def __contains__(self, item: object) -> bool:
        """Whether the log contains a given event id or ``Event`` (by id).

        Checking by id (not the ``Sequence`` default of value-equality) keeps
        ``event in events`` true after the event is stamped with a ``parent_id``
        on append, and avoids hashing unhashable event payloads.
        """
        if isinstance(item, Event):
            return item.id in self._id_to_idx
        return item in self._id_to_idx

    def _effective_parent_id(self, idx: int, event: Event) -> EventID | None:
        """Resolve the parent of ``event`` (at ``idx``) for tree traversal.

        Legacy events predating the tree have no ``parent_id``; they fall back to
        the linear chain (event ``idx - 1``) so old conversations load unbranched
        with no disk rewrite. An explicit parent whose file was lost is bridged
        the same way, so the branch keeps its surviving ancestors (the system
        prompt above all) instead of ending at the hole.
        """
        parent_id = event.parent_id
        if parent_id == ROOT_PARENT_ID:
            return None  # explicit root (feature-created root at idx > 0)
        if parent_id is not None:
            if parent_id in self._id_to_idx:
                return parent_id  # explicit (new events)
            self._report_missing(parent_id)
        if idx == 0:
            return None  # genuine root
        return self.get_id(idx - 1)  # legacy linear chain, or bridge over a hole

    def _report_missing(self, event_id: EventID) -> None:
        """Warn once per id that the log references an event whose file is gone."""
        if event_id in self._reported_missing:
            return
        self._reported_missing.add(event_id)
        logger.warning(
            "Event '%s' is missing from the event log (its file was lost).",
            event_id,
        )

    def path_to_root(
        self, leaf_id: EventID | None, limit: int | None = None
    ) -> list[Event]:
        """The active branch ``leaf -> ... -> root``, returned root-first.

        ``leaf_id=None`` yields ``[]``. ``limit`` keeps only the last ``limit``
        events (walking back from the leaf), so callers wanting a recent window
        stay O(limit) instead of O(branch). Raises ValueError on a cycle. An
        ancestor whose file was lost is bridged (see ``_effective_parent_id``);
        a ``leaf_id`` missing from the log yields ``[]``.
        """
        chain: list[Event] = []
        seen: set[EventID] = set()
        cur_id: EventID | None = leaf_id
        while cur_id is not None and (limit is None or len(chain) < limit):
            if cur_id in seen:
                raise ValueError(f"Cycle in event tree at {cur_id}")
            seen.add(cur_id)
            idx = self._id_to_idx.get(cur_id)
            if idx is None:
                # Only the leaf can be unknown: ancestors are bridged.
                self._report_missing(cur_id)
                break
            chain.append(evt := self[idx])
            cur_id = self._effective_parent_id(idx, evt)
        return chain[::-1]

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

        if (cached := self._event_cache.get(i)) is not None:
            return cached

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
        evt = Event.model_validate_json(txt)
        self._event_cache[i] = evt
        return evt

    def __iter__(self) -> Iterator[Event]:
        for i in range(self._length):
            cached = self._event_cache.get(i)
            if cached is not None:
                yield cached
                continue
            txt = self._fs.read(self._path(i))
            if not txt:
                continue
            evt = Event.model_validate_json(txt)
            evt_id = evt.id
            if i not in self._idx_to_id:
                self._idx_to_id[i] = evt_id
                self._id_to_idx.setdefault(evt_id, i)
            self._event_cache[i] = evt
            yield evt

    def append(self, event: Event) -> int:
        """Append an event with locking for thread/process safety.

        Returns:
            The sequence number (index) the log assigned to ``event``.

        Raises:
            TimeoutError: If the lock cannot be acquired within LOCK_TIMEOUT_SECONDS.
            ValueError: If an event with the same ID already exists or its explicit
                parent does not exist.
        """
        evt_id = event.id

        try:
            with self._fs.lock(self._lock_path, timeout=LOCK_TIMEOUT_SECONDS):
                # Sync with disk only if the marker cannot rule out another writer
                if not self._marker_matches_length():
                    disk_length = self._count_events_on_disk()
                    if disk_length > self._length:
                        self._sync_from_disk(disk_length)

                if evt_id in self._id_to_idx:
                    existing_idx = self._id_to_idx[evt_id]
                    raise ValueError(
                        f"Event with ID '{evt_id}' already exists at index "
                        f"{existing_idx}"
                    )

                if (
                    event.parent_id not in (None, ROOT_PARENT_ID)
                    and event.parent_id not in self._id_to_idx
                ):
                    raise ValueError(
                        f"Parent event '{event.parent_id}' does not exist "
                        f"for event '{evt_id}'"
                    )

                payload = event.model_dump_json(exclude_none=True)
                write_guard = (
                    nullcontext() if self._write_guard is None else self._write_guard()
                )
                idx = self._length
                # Never reuse an on-disk index: behind a lost file the dense
                # index lags it, and a shared index makes the reload order
                # depend on the event ids.
                disk_idx = max(idx, self._next_disk_idx)
                with write_guard:
                    self._fs.write(self._file_path(disk_idx, evt_id), payload)
                if disk_idx != idx:
                    self._disk_idx[idx] = disk_idx
                self._next_disk_idx = disk_idx + 1
                self._idx_to_id[idx] = evt_id
                self._id_to_idx[evt_id] = idx
                self._event_cache[idx] = event
                self._length += 1
                self._advance_length_marker(idx)
                return idx
        except TimeoutError:
            logger.error(
                f"Failed to acquire EventLog lock within {LOCK_TIMEOUT_SECONDS}s "
                f"for event {evt_id}"
            )
            raise

    def _marker_path(self, length: int) -> str:
        return f"{self._dir}/{LENGTH_MARKER_PATTERN.format(length=length)}"

    def _marker_matches_length(self) -> bool:
        """Whether the marker proves no one has appended since we last did.

        ``False`` is not proof of divergence, so callers must fall back to the
        exact count.
        """
        try:
            return bool(self._fs.exists(self._marker_path(self._length)))
        except Exception as e:
            logger.warning("Error reading event count marker in %s: %s", self._dir, e)
            return False

    def _advance_length_marker(self, previous_length: int) -> None:
        """Move the marker from ``previous_length`` to the current length.

        Deleting first is deliberate: an interrupted update leaves no marker
        rather than a stale one claiming to be current.
        """
        try:
            self._fs.delete(self._marker_path(previous_length))
            self._fs.write(self._marker_path(self._length), "")
        except Exception as e:
            logger.warning("Error updating event count marker in %s: %s", self._dir, e)

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
            if posix_path_name(p).startswith("event-") and p.endswith(".json")
        )

    def _sync_from_disk(self, disk_length: int) -> None:
        """Sync state for events written by other processes.

        Preserves existing index mappings and only scans new events.
        """
        # Preserve existing mappings
        existing = [
            (idx, evt_id, self._disk_idx.get(idx, idx))
            for idx, evt_id in self._idx_to_id.items()
        ]

        # Re-scan to pick up new events
        scanned_length = self._scan_and_build_index()

        # Restore mappings the scan cannot see (e.g., for non-UUID event IDs).
        # Only whole pairs, and only when neither side was rescanned: behind a
        # lost file a rescanned index can belong to a different event.
        for idx, evt_id, disk_idx in existing:
            if idx in self._idx_to_id or evt_id in self._id_to_idx:
                continue
            self._idx_to_id[idx] = evt_id
            self._id_to_idx[evt_id] = idx
            if disk_idx != idx:
                self._disk_idx[idx] = disk_idx
            self._next_disk_idx = max(self._next_disk_idx, disk_idx + 1)

        # Use the higher of scanned length or disk_length
        self._length = max(scanned_length, disk_length)

    def __len__(self) -> int:
        return self._length

    def _path(self, idx: int, *, event_id: EventID | None = None) -> str:
        return self._file_path(
            self._disk_idx.get(idx, idx), event_id or self._idx_to_id[idx]
        )

    def _file_path(self, disk_idx: int, event_id: EventID) -> str:
        name = EVENT_FILE_PATTERN.format(idx=disk_idx, event_id=event_id)
        return f"{self._dir}/{name}"

    def _reset_index(self) -> None:
        self._id_to_idx.clear()
        self._idx_to_id.clear()
        self._disk_idx.clear()
        self._event_cache.clear()
        self._next_disk_idx = 0

    def _scan_and_build_index(self) -> int:
        try:
            paths = self._fs.list(self._dir)
        except Exception:
            self._reset_index()
            return 0

        found: list[tuple[int, EventID]] = []
        for p in paths:
            name = posix_path_name(p)
            m = EVENT_NAME_RE.match(name)
            if m:
                found.append((int(m.group("idx")), m.group("event_id")))
            elif name.startswith("."):
                # Our own sidecars (lock file, count marker), not event files.
                continue
            else:
                logger.warning(f"Unrecognized event file name: {name}")

        self._reset_index()
        # Index every event file in on-disk order instead of stopping at the
        # first missing index, so one lost file does not make every later event
        # unknown. The dense index closes the hole; the on-disk index stays the
        # event's file name and seq.
        found.sort()
        holes: list[tuple[int, int]] = []
        lost = 0
        for i, (disk_idx, evt_id) in enumerate(found):
            if disk_idx > self._next_disk_idx:
                lost += disk_idx - self._next_disk_idx
                if len(holes) < MAX_REPORTED_HOLES:
                    holes.append((self._next_disk_idx, disk_idx - 1))
            self._next_disk_idx = disk_idx + 1
            self._idx_to_id[i] = evt_id
            if disk_idx != i:
                self._disk_idx[i] = disk_idx
            if evt_id in self._id_to_idx:
                logger.warning(
                    f"Duplicate event ID '{evt_id}' found during scan. "
                    f"Keeping first occurrence at index {self._id_to_idx[evt_id]}, "
                    f"ignoring duplicate at index {i}"
                )
            else:
                self._id_to_idx[evt_id] = i
        if lost != self._reported_lost:
            self._reported_lost = lost
            if lost:
                listed = ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in holes)
                logger.warning(
                    "Event log %s is missing %d event file(s) (on-disk index %s%s); "
                    "the %d surviving events stay readable.",
                    self._dir,
                    lost,
                    listed,
                    ", ..." if lost > sum(b - a + 1 for a, b in holes) else "",
                    len(found),
                )
        return len(found)
