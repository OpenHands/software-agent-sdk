"""Comprehensive edge case tests for EventLog class."""

from unittest.mock import Mock

import pytest

from openhands.sdk.conversation.event_store import LOCK_FILE_NAME, EventLog
from openhands.sdk.conversation.persistence_const import (
    EVENT_FILE_PATTERN,
    EVENT_NAME_RE,
    EVENTS_DIR,
)
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io.memory import InMemoryFileStore
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.utils.path import posix_path_name


def create_test_event(event_id: str, content: str = "Test content") -> MessageEvent:
    """Create a test MessageEvent with specific ID."""
    event = MessageEvent(
        id=event_id,
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )
    return event


def test_event_log_empty_initialization():
    """Test EventLog with empty file store."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    assert len(log) == 0
    assert list(log) == []

    # Test accessing empty log
    with pytest.raises(IndexError):
        log[0]

    with pytest.raises(IndexError):
        log[-1]


def test_event_log_append_returns_assigned_index():
    """``append`` returns the sequence number it assigned, so callers don't
    need a separate ``get_index`` lookup right after appending."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    assert log.append(create_test_event("event-1")) == 0
    assert log.append(create_test_event("event-2")) == 1
    assert log.append(create_test_event("event-3")) == 2


def test_event_log_id_validation_duplicate_id():
    """Test that duplicate event IDs are prevented."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    event1 = create_test_event("test-id-1", "First event")
    event2 = create_test_event("test-id-1", "Second event with same ID")

    log.append(event1)

    # Duplicate IDs should raise ValueError
    with pytest.raises(
        ValueError, match="Event with ID 'test-id-1' already exists at index 0"
    ):
        log.append(event2)

    assert len(log) == 1


def test_event_log_id_validation_existing_id_different_index():
    """Test behavior when internal state is manually modified."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    # Add first event
    event1 = create_test_event("event-1", "First")
    log.append(event1)

    # Manually corrupt the internal state to simulate edge case
    log._id_to_idx["event-2"] = 0  # Wrong index for event-2

    # With duplicate prevention, event-2 will be rejected because
    # "event-2" is already in _id_to_idx
    event2 = create_test_event("event-2", "Second")
    with pytest.raises(
        ValueError, match="Event with ID 'event-2' already exists at index 0"
    ):
        log.append(event2)

    # Only the first event should be in the log
    assert len(log) == 1


def test_event_log_negative_indexing():
    """Test negative indexing works correctly."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    events = [
        create_test_event("event-1", "First"),
        create_test_event("event-2", "Second"),
        create_test_event("event-3", "Third"),
    ]

    for event in events:
        log.append(event)

    # Test negative indexing
    assert log[-1].id == "event-3"
    assert log[-2].id == "event-2"
    assert log[-3].id == "event-1"

    # Test out of bounds negative indexing
    with pytest.raises(IndexError):
        log[-4]


def test_event_log_get_index_and_get_id():
    """Test get_index and get_id methods."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    events = [
        create_test_event("alpha", "First"),
        create_test_event("beta", "Second"),
        create_test_event("gamma", "Third"),
    ]

    for event in events:
        log.append(event)

    # Test get_index
    assert log.get_index("alpha") == 0
    assert log.get_index("beta") == 1
    assert log.get_index("gamma") == 2

    # Test get_id
    assert log.get_id(0) == "alpha"
    assert log.get_id(1) == "beta"
    assert log.get_id(2) == "gamma"

    # Test negative indexing in get_id
    assert log.get_id(-1) == "gamma"
    assert log.get_id(-2) == "beta"
    assert log.get_id(-3) == "alpha"

    # Test errors
    with pytest.raises(KeyError, match="Unknown event_id: nonexistent"):
        log.get_index("nonexistent")

    with pytest.raises(IndexError, match="Event index out of range"):
        log.get_id(3)

    with pytest.raises(IndexError, match="Event index out of range"):
        log.get_id(-4)


def test_event_log_missing_event_file():
    """Test behavior when event file is missing."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    event = create_test_event("test-event", "Content")
    log.append(event)

    # Manually delete the file to simulate corruption
    path = log._path(0, event_id="test-event")
    fs.delete(path)

    # Cached access still works — the event was cached on append
    assert log[0].id == "test-event"

    # Clear cache to test the disk-access path
    log._event_cache.clear()
    with pytest.raises(FileNotFoundError):
        log[0]


def test_event_log_corrupted_json_in_file():
    """Test behavior with corrupted JSON in event file."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    # Manually create a corrupted event file
    fs.write("events/event-00000-test-id.json", "invalid json content")

    # Force rescan
    log._length = log._scan_and_build_index()

    # The corrupted file should not be indexed, so length should be 0
    assert len(log) == 0

    # Accessing should raise IndexError since no valid events exist
    with pytest.raises(IndexError):
        log[0]


def test_event_log_clear_functionality():
    """Test that EventLog doesn't have a clear method in current implementation."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    events = [
        create_test_event("event-1", "First"),
        create_test_event("event-2", "Second"),
        create_test_event("event-3", "Third"),
    ]

    for event in events:
        log.append(event)

    assert len(log) == 3

    # Current implementation doesn't have a clear method
    assert not hasattr(log, "clear")

    # Events should still be accessible
    assert len(log) == 3
    assert log._id_to_idx != {}
    assert log._idx_to_id != {}


def test_event_log_index_gaps_detection():
    """A gap must not discard every later event: survivors are reindexed (#6174)."""
    fs = InMemoryFileStore()
    log = EventLog(fs)
    log.append(create_test_event("aaaaaaaa-0000", "Event 0"))
    log.append(create_test_event("bbbbbbbb-0000", "Event 1"))
    log.append(create_test_event("cccccccc-0000", "Event 2"))

    # Create a gap (missing event-00001) and rescan from disk.
    fs.delete(log._path(1, event_id="bbbbbbbb-0000"))
    reloaded = EventLog(fs)

    # Every surviving event file is indexed, densely renumbered in file order,
    # so one lost file no longer makes the rest of the log unknown.
    assert len(reloaded) == 2
    assert reloaded[0].id == "aaaaaaaa-0000"
    assert reloaded[1].id == "cccccccc-0000"
    assert reloaded.get_index("cccccccc-0000") == 1
    assert reloaded.get_id(1) == "cccccccc-0000"


def test_event_log_file_store_exceptions():
    """Test handling of file store exceptions."""
    import tempfile

    mock_fs = Mock()
    mock_fs.list.side_effect = Exception("File system error")
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_fs.get_absolute_path.return_value = f"{temp_dir}/.eventlog.lock"
        log = EventLog(mock_fs)
        assert len(log) == 0


def test_event_log_iteration_with_missing_files():
    """Test iteration behavior when some files are missing."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    # Add events
    events = [
        create_test_event("event-1", "First"),
        create_test_event("event-2", "Second"),
        create_test_event("event-3", "Third"),
    ]

    for event in events:
        log.append(event)

    # Delete middle file
    path = log._path(1, event_id="event-2")
    fs.delete(path)

    # Cached iteration still works — all 3 events were cached on append
    assert [e.id for e in log] == ["event-1", "event-2", "event-3"]

    # Clear cache to test the disk-access path
    log._event_cache.clear()
    with pytest.raises(FileNotFoundError):
        list(log)


def test_event_log_iteration_backfills_missing_mappings():
    """Test that iteration fails when mappings are missing and cache is cold."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    # Add an event through normal append
    event = create_test_event("manual-event", "Manual event")
    log.append(event)

    # Verify the event was added
    assert len(log) == 1
    assert log[0].id == "manual-event"

    # Clear mappings and cache to simulate missing data
    log._idx_to_id.clear()
    log._id_to_idx.clear()
    log._event_cache.clear()

    # But keep the length so iteration can work
    log._length = 1

    # Without cache or mappings, iteration fails on _path lookup
    with pytest.raises(KeyError):
        list(log)

    # Mappings remain empty
    assert 0 not in log._idx_to_id
    assert "manual-event" not in log._id_to_idx


def test_event_log_custom_directory():
    """Test EventLog with custom directory."""
    fs = InMemoryFileStore()
    custom_dir = "custom_events"
    log = EventLog(fs, custom_dir)

    event = create_test_event("custom-event", "Custom content")
    log.append(event)

    # Should create file in custom directory - check by listing files
    files = fs.list(custom_dir)
    assert len(files) > 0
    assert any("custom-event" in f for f in files)

    # Should be able to read back
    assert len(log) == 1
    assert log[0].id == "custom-event"


def test_event_log_large_index_formatting():
    """Test proper formatting of large indices."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    # Simulate large index by manually setting length
    log._length = 99999

    event = create_test_event("large-index-event", "Content")
    log.append(event)

    # Should format with proper zero-padding - check by listing files
    files = fs.list("events")
    assert len(files) > 0
    assert any("event-99999-large-index-event" in f for f in files)

    assert log.get_index("large-index-event") == 99999
    assert log.get_id(99999) == "large-index-event"


def test_event_log_concurrent_append_thread_safety():
    """Test concurrent appends from multiple threads."""
    import tempfile
    import threading

    from openhands.sdk.io.local import LocalFileStore

    with tempfile.TemporaryDirectory() as temp_dir:
        fs = LocalFileStore(temp_dir)
        log = EventLog(fs)
        errors: list[Exception] = []
        lock = threading.Lock()

        def append_events(thread_id: int, num_events: int):
            for i in range(num_events):
                try:
                    event = create_test_event(
                        f"t{thread_id}-e{i}", f"Thread {thread_id}"
                    )
                    log.append(event)
                except Exception as e:
                    with lock:
                        errors.append(e)

        threads = []
        for t_id in range(5):
            t = threading.Thread(target=append_events, args=(t_id, 10))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(log) == 50


def test_event_log_concurrent_writes_serialized():
    """Test two EventLog instances serialize writes correctly."""
    import tempfile

    from openhands.sdk.io.local import LocalFileStore

    with tempfile.TemporaryDirectory() as temp_dir:
        fs = LocalFileStore(temp_dir)
        log1 = EventLog(fs)
        log2 = EventLog(fs)

        log1.append(create_test_event("event-1", "First"))
        log2.append(create_test_event("event-2", "Second"))

        assert log1._length == 1
        assert log2._length == 2

        # Sidecars (lock file, event-count marker) are dotfiles, not events.
        files = [f for f in fs.list("events") if not posix_path_name(f).startswith(".")]
        assert len(files) == 2


def test_get_single_item_recovers_from_stale_index():
    """_get_single_item rebuilds the index when _idx_to_id is stale."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    # Use UUID-like IDs to match EVENT_NAME_RE pattern
    evt_id = "00000000-0000-0000-0000-000000000001"
    event = create_test_event(evt_id, "Should recover")
    log.append(event)
    assert log[0].id == evt_id

    # Simulate a stale in-memory index (e.g., external file modification)
    log._idx_to_id.clear()
    log._id_to_idx.clear()

    # Access should rebuild the index transparently and succeed
    recovered = log[0]
    assert recovered.id == evt_id


def test_get_single_item_stale_index_out_of_range():
    """After index rebuild, raise IndexError if the index no longer exists."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    evt_id = "00000000-0000-0000-0000-000000000002"
    event = create_test_event(evt_id, "Only one")
    log.append(event)

    # Clear index AND artificially inflate length to simulate stale state
    log._idx_to_id.clear()
    log._id_to_idx.clear()
    log._length = 5  # pretend there are 5 events

    # Index 3 doesn't exist on disk; should raise IndexError after rebuild
    with pytest.raises(IndexError, match="Event index out of range"):
        log[3]


def test_event_cache_eliminates_repeated_deserialization():
    """Repeated access to the same event returns the cached object."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    event = create_test_event("cached-event", "Hello")
    log.append(event)

    first = log[0]
    second = log[0]
    # Same object identity — no re-deserialization
    assert first is second


def test_event_cache_populated_by_iteration():
    """Iterating the log populates the cache for subsequent indexed access."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    for i in range(3):
        log.append(create_test_event(f"evt-{i}", f"Content {i}"))

    # Clear cache to force cold iteration
    log._event_cache.clear()
    events_from_iter = list(log)

    # Cache should now hold all 3 events
    assert len(log._event_cache) == 3

    # Indexed access returns the same cached object
    for i, evt in enumerate(events_from_iter):
        assert log[i] is evt


def test_event_cache_survives_across_multiple_iterations():
    """Multiple iterations return the same cached objects."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    log.append(create_test_event("a", "A"))
    log.append(create_test_event("b", "B"))

    first_pass = list(log)
    second_pass = list(log)
    assert all(a is b for a, b in zip(first_pass, second_pass, strict=True))


@pytest.mark.parametrize("idx", [0, 1, 99999, 100000, 100001, 999999, 1_000_000])
def test_event_filename_writer_reader_agree(idx):
    """The reader regex must match the exact filename the writer emits."""
    event_id = f"{idx:08x}-0000-0000-0000-000000000000"
    name = EVENT_FILE_PATTERN.format(idx=idx, event_id=event_id)

    m = EVENT_NAME_RE.match(name)
    assert m is not None, f"reader regex failed to match writer output: {name!r}"
    assert int(m.group("idx")) == idx


def test_event_log_cold_reload_past_100k_events():
    """A log with >100000 events reloads without dropping the tail.

    Before the fix the cold scan truncated the index at 100000 (silent data
    loss) and the count/scan paths diverged, corrupting length accounting.
    """
    # Raise the test-double LRU caps so the in-memory store keeps every file;
    # this configures only the storage stand-in, not the code under test.
    fs = InMemoryFileStore(max_size=500_000, max_memory=2 * 1024 * 1024 * 1024)
    payload = create_test_event("seed", "seed").model_dump_json(exclude_none=True)

    n = 100_002  # straddles the 5-/6-digit boundary
    for i in range(n):
        event_id = f"{i:08x}-0000-0000-0000-000000000000"
        path = f"{EVENTS_DIR}/" + EVENT_FILE_PATTERN.format(idx=i, event_id=event_id)
        fs.write(path, payload)

    log = EventLog(fs)

    # No silent truncation: every event survives the cold scan.
    assert len(log) == n

    # The events straddling the boundary are readable, not unreachable holes.
    assert log[99999] is not None
    assert log[100000] is not None
    assert log[100001] is not None

    # Appending after reload keeps length accounting consistent.
    log.append(create_test_event("after-reload", "after"))
    assert len(log) == n + 1


def test_append_does_not_scan_the_directory_as_the_log_grows():
    """Append cost must not grow with conversation length (issue #4676).

    The multi-writer sync used to count every file in the events directory on
    every append, so appending event N cost O(N) directory work while holding
    the lock. Listing calls are the proxy for that cost.
    """
    fs = InMemoryFileStore()
    log = EventLog(fs)

    listings: list[str] = []
    original_list = fs.list

    def counting_list(path: str) -> list[str]:
        listings.append(path)
        return original_list(path)

    fs.list = counting_list  # type: ignore[method-assign]

    for i in range(50):
        log.append(create_test_event(f"{i:08x}-0000-0000-0000-000000000000"))

    # The first append has no marker to go on and pays for one listing; every
    # later append is answered by the marker alone.
    assert len(listings) <= 1, f"append listed the events dir {len(listings)} times"
    assert len(log) == 50


def test_append_still_syncs_when_another_writer_appended():
    """A second writer's events are still picked up, marker or not."""
    fs = InMemoryFileStore()
    log1 = EventLog(fs)
    log2 = EventLog(fs)

    log1.append(create_test_event("00000000-0000-0000-0000-000000000001"))
    log1.append(create_test_event("00000000-0000-0000-0000-000000000002"))

    # log2 opened at length 0 and never saw those appends; the marker no longer
    # names its length, so it falls back to counting and lands after them.
    log2.append(create_test_event("00000000-0000-0000-0000-000000000003"))

    assert log2._length == 3
    assert log2.get_index("00000000-0000-0000-0000-000000000003") == 2


def test_append_syncs_when_marker_is_missing():
    """A log written before the marker existed keeps the exact-count path."""
    fs = InMemoryFileStore()
    log = EventLog(fs)
    log.append(create_test_event("00000000-0000-0000-0000-000000000001"))

    # Simulate a legacy on-disk log: events present, no marker.
    for path in list(fs.files.keys()):
        if posix_path_name(path).startswith("."):
            del fs.files[path]

    reopened = EventLog(fs)
    reopened.append(create_test_event("00000000-0000-0000-0000-000000000002"))
    assert len(reopened) == 2

    # And the marker is re-established for the appends that follow.
    assert fs.exists(reopened._marker_path(2))


def test_scan_does_not_warn_about_sidecar_files(caplog):
    """The lock file and count marker are ours; they are not malformed events."""
    fs = InMemoryFileStore()
    log = EventLog(fs)
    log.append(create_test_event("00000000-0000-0000-0000-000000000001"))
    fs.write(f"{EVENTS_DIR}/{LOCK_FILE_NAME}", "")

    with caplog.at_level("WARNING"):
        EventLog(fs)

    assert "Unrecognized event file name" not in caplog.text


def _child(event_id: str, parent_id: str) -> MessageEvent:
    return create_test_event(event_id, event_id).model_copy(
        update={"parent_id": parent_id}
    )


def _log_with_lost_file(lost: int, n: int = 3) -> InMemoryFileStore:
    """Append ``n`` events (aaaaaaaa-0000, bbbbbbbb-0000, ...), then lose one file."""
    fs = InMemoryFileStore()
    log = EventLog(fs)
    for c in "abcdefgh"[:n]:
        log.append(create_test_event(f"{c * 8}-0000", c))
    fs.delete(log._path(lost))
    return fs


def test_log_survives_missing_middle_file():
    """Deleting one event file must not make the rest of the log unknown (#6174)."""
    fs = _log_with_lost_file(1)

    reloaded = EventLog(fs)
    assert [e.id for e in reloaded] == ["aaaaaaaa-0000", "cccccccc-0000"]
    assert reloaded.get_index("cccccccc-0000") == 1
    reloaded._event_cache.clear()
    assert reloaded[1].id == "cccccccc-0000"  # read back from its on-disk file

    # The next append takes a fresh on-disk index: sharing the survivor's would
    # let an id that sorts first swap the two on the next scan.
    assert reloaded.append(create_test_event("11111111-0000", "new")) == 2
    names = sorted(posix_path_name(p) for p in fs.list(EVENTS_DIR))
    assert "event-00003-11111111-0000.json" in names
    assert [e.id for e in EventLog(fs)] == [
        "aaaaaaaa-0000",
        "cccccccc-0000",
        "11111111-0000",
    ]


def test_seq_is_the_on_disk_index_and_survives_a_reload():
    """Behind a lost file the dense index shifts, the seq does not."""
    fs = InMemoryFileStore()
    log = EventLog(fs)
    for c in "abcd":
        log.append(create_test_event(f"{c * 8}-0000", c))
    assert [log.get_seq(i) for i in range(4)] == [0, 1, 2, 3]
    assert log._disk_idx == {}  # an intact log records no overrides

    fs.delete(log._path(1))
    reloaded = EventLog(fs)
    assert [reloaded.get_seq(i) for i in range(3)] == [0, 2, 3]
    assert reloaded.append(create_test_event("eeeeeeee-0000", "e")) == 3
    assert reloaded.get_seq(3) == 4  # a seq a client has seen is never reused
    assert reloaded.index_after_seq(-1) == 0
    assert reloaded.index_after_seq(1) == 1  # seq 1 is lost: resume at seq 2
    assert reloaded.index_after_seq(4) == 4


def test_duplicate_id_file_keeps_its_index():
    """A second file with a known id stays an entry, so the scanned length
    matches the file count and an unmarked append stays consistent."""
    fs = InMemoryFileStore()
    log = EventLog(fs)
    log.append(create_test_event("aaaaaaaa-0000", "a"))
    log.append(create_test_event("bbbbbbbb-0000", "b"))
    fs.write(f"{EVENTS_DIR}/event-00002-aaaaaaaa-0000.json", fs.read(log._path(0)))
    for path in list(fs.files.keys()):
        if posix_path_name(path).startswith("."):
            del fs.files[path]

    reloaded = EventLog(fs)
    assert reloaded.append(create_test_event("dddddddd-0000", "d")) == 3
    assert [e.id for e in reloaded] == [
        "aaaaaaaa-0000",
        "bbbbbbbb-0000",
        "aaaaaaaa-0000",
        "dddddddd-0000",
    ]
    assert reloaded.get_index("aaaaaaaa-0000") == 0


def test_sync_does_not_map_a_lost_id_onto_another_event():
    """A writer that still holds a lost event must not restore its id onto the
    index a rescan gave to a different event (a false cycle)."""
    fs = InMemoryFileStore()
    a = EventLog(fs)
    a.append(create_test_event("aaaaaaaa-0000", "a"))
    a.append(_child("bbbbbbbb-0000", "aaaaaaaa-0000"))
    a.append(_child("cccccccc-0000", "bbbbbbbb-0000"))
    fs.delete(a._path(1))  # lost while writer a still runs

    b = EventLog(fs)  # a second writer loads the damaged log
    b.append(_child("dddddddd-0000", "cccccccc-0000"))
    b.append(_child("eeeeeeee-0000", "dddddddd-0000"))
    a.append(_child("ffffffff-0000", "eeeeeeee-0000"))  # a syncs from disk

    assert "bbbbbbbb-0000" not in a
    assert [e.id[:1] for e in a.path_to_root("ffffffff-0000")] == list("acdef")


@pytest.mark.parametrize("lost", [0, 1], ids=["head", "middle"])
def test_lost_event_file_is_reported_once(caplog, lost):
    fs = _log_with_lost_file(lost)

    with caplog.at_level("WARNING"):
        log = EventLog(fs)
        log._length = log._scan_and_build_index()  # a rescan, as a sync does

    messages = [r.getMessage() for r in caplog.records]
    warnings = [m for m in messages if "is missing 1 event file" in m]
    assert len(warnings) == 1
    assert f"(on-disk index {lost})" in warnings[0]


def test_far_stray_index_is_summarized_not_enumerated(caplog):
    """One file with a huge index must not cost memory proportional to it."""
    fs = InMemoryFileStore()
    log = EventLog(fs)
    log.append(create_test_event("aaaaaaaa-0000", "a"))
    stray = fs.read(log._path(0)).replace("aaaaaaaa-0000", "bbbbbbbb-0000")
    fs.write(f"{EVENTS_DIR}/event-1000000000-bbbbbbbb-0000.json", stray)

    with caplog.at_level("WARNING"):
        reloaded = EventLog(fs)

    assert reloaded.get_seq(1) == 1_000_000_000
    assert "missing 999999999 event file(s) (on-disk index 1-999999999)" in (
        caplog.text
    )


def test_path_to_root_bridges_a_missing_ancestor(caplog):
    """A lost parent is bridged to the preceding event, so the branch keeps its
    root (in a conversation, the system prompt) instead of ending at the hole."""
    fs = InMemoryFileStore()
    log = EventLog(fs)
    log.append(create_test_event("aaaaaaaa-0000", "a"))
    log.append(_child("bbbbbbbb-0000", "aaaaaaaa-0000"))
    log.append(_child("cccccccc-0000", "bbbbbbbb-0000"))
    fs.delete(log._path(1))

    reloaded = EventLog(fs)
    with caplog.at_level("WARNING"):
        for _ in range(3):
            branch = reloaded.path_to_root("cccccccc-0000")
            assert [e.id for e in branch] == ["aaaaaaaa-0000", "cccccccc-0000"]

    assert caplog.text.count("bbbbbbbb-0000") == 1  # once, not on every walk
    # A fully unknown leaf yields an empty branch, not a KeyError
    assert reloaded.path_to_root("dddddddd-0000") == []


def test_path_to_root_legacy_chain_spans_a_gap():
    """Legacy events without parent_id chain to their dense neighbour, so the
    branch spans the hole left by a lost file."""
    fs = InMemoryFileStore()
    log = EventLog(fs)
    log.append(create_test_event("aaaaaaaa-0000", "First"))
    log.append(create_test_event("bbbbbbbb-0000", "Second"))
    log.append(create_test_event("cccccccc-0000", "Third"))

    fs.delete(log._path(1, event_id="bbbbbbbb-0000"))

    reloaded = EventLog(fs)
    # parent_id=None falls back to the previous indexed event: aaaaaaaa -> cccccc
    assert [e.id for e in reloaded.path_to_root("cccccccc-0000")] == [
        "aaaaaaaa-0000",
        "cccccccc-0000",
    ]
