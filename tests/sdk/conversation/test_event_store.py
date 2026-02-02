"""Comprehensive edge case tests for EventLog class."""

import json
from unittest.mock import Mock

import pytest

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io.memory import InMemoryFileStore
from openhands.sdk.llm import Message, TextContent


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

    # Accessing the event should raise FileNotFoundError
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
    """Test detection and handling of index gaps."""
    fs = InMemoryFileStore()

    # Create files with gaps (missing event-00001)
    event0 = {
        "id": "event-0",
        "llm_message": {
            "role": "user",
            "content": [{"type": "text", "text": "Event 0"}],
        },
        "source": "user",
        "kind": "openhands.sdk.event.llm_convertible.MessageEvent",
    }
    fs.write("events/event-00000-event-0.json", json.dumps(event0))

    event2 = {
        "id": "event-2",
        "llm_message": {
            "role": "user",
            "content": [{"type": "text", "text": "Event 2"}],
        },
        "source": "user",
        "kind": "openhands.sdk.event.llm_convertible.MessageEvent",
    }
    fs.write("events/event-00002-event-2.json", json.dumps(event2))

    # Should only load up to the gap
    log = EventLog(fs)

    # The current scanning logic is very strict about gaps
    # If there's a gap at any index, it stops loading events entirely
    # This is the current behavior, though it could be improved
    assert len(log) == 0  # No events loaded due to gap detection


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

    # Iteration will fail when it hits the missing file
    # This is expected behavior - the EventLog expects all files to exist
    with pytest.raises(FileNotFoundError):
        list(log)


def test_event_log_iteration_backfills_missing_mappings():
    """Test that iteration fails when mappings are missing."""
    fs = InMemoryFileStore()
    log = EventLog(fs)

    # Add an event through normal append
    event = create_test_event("manual-event", "Manual event")
    log.append(event)

    # Verify the event was added
    assert len(log) == 1
    assert log[0].id == "manual-event"

    # Clear mappings to simulate missing data
    log._idx_to_id.clear()
    log._id_to_idx.clear()

    # But keep the length so iteration can work
    log._length = 1

    # Current implementation doesn't backfill mappings, so iteration fails
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

        files = [f for f in fs.list("events") if not f.endswith(".lock")]
        assert len(files) == 2
