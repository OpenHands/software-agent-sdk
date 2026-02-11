"""Tests for browser session recording flush behavior.

These tests verify that:
1. Recording events are periodically flushed to new file chunks
"""

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from openhands.tools.browser_use.event_storage import EventStorage
from openhands.tools.browser_use.recording import (
    DEFAULT_CONFIG,
    RecordingSession,
)
from openhands.tools.browser_use.server import CustomBrowserUseServer


# Get default config values for tests
RECORDING_FLUSH_INTERVAL_SECONDS = DEFAULT_CONFIG.flush_interval_seconds


@pytest.fixture
def mock_cdp_session():
    """Create a mock CDP session."""
    cdp_session = MagicMock()
    cdp_session.session_id = "test-session-id"
    cdp_session.cdp_client = MagicMock()
    cdp_session.cdp_client.send = MagicMock()
    cdp_session.cdp_client.send.Runtime = MagicMock()
    cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock()
    return cdp_session


@pytest.fixture
def mock_browser_session(mock_cdp_session):
    """Create a mock browser session."""
    browser_session = MagicMock()
    browser_session.get_or_create_cdp_session = AsyncMock(return_value=mock_cdp_session)
    return browser_session


@pytest.fixture
def server_with_mock_browser(mock_browser_session):
    """Create a CustomBrowserUseServer with mocked browser session."""
    server = CustomBrowserUseServer()
    server.browser_session = mock_browser_session
    return server


@pytest.fixture
def recording_session_with_mock_browser(mock_browser_session):
    """Create a RecordingSession with mocked browser session."""
    return mock_browser_session, RecordingSession()


def create_mock_events(count: int, size_per_event: int = 100) -> list[dict]:
    """Create mock rrweb events with specified count and approximate size."""
    events = []
    for i in range(count):
        # Create event with padding to reach approximate size
        padding = "x" * max(0, size_per_event - 50)
        events.append(
            {
                "type": 3,
                "timestamp": 1000 + i,
                "data": {"source": 1, "text": padding},
            }
        )
    return events


class TestEventStorage:
    """Tests for EventStorage - no browser mocks needed."""

    def test_save_events_creates_file(self):
        """Test that save_events creates a JSON file with events."""
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = EventStorage(output_dir=temp_dir)
            storage.create_session_subfolder()

            events = create_mock_events(10)
            filepath = storage.save_events(events)

            assert filepath is not None
            assert os.path.exists(filepath)
            with open(filepath) as f:
                saved = json.load(f)
            assert len(saved) == 10

    def test_save_events_updates_counters(self):
        """Test that save_events updates file_count and total_events."""
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = EventStorage(output_dir=temp_dir)
            storage.create_session_subfolder()

            storage.save_events(create_mock_events(5))
            assert storage.file_count == 1
            assert storage.total_events == 5

            storage.save_events(create_mock_events(10))
            assert storage.file_count == 2
            assert storage.total_events == 15

    def test_save_events_returns_none_without_session_dir(self):
        """Test that save_events returns None if no session_dir is set."""
        storage = EventStorage()
        result = storage.save_events(create_mock_events(5))
        assert result is None

    def test_save_events_returns_none_for_empty_events(self):
        """Test that save_events returns None for empty event list."""
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = EventStorage(output_dir=temp_dir)
            storage.create_session_subfolder()
            result = storage.save_events([])
            assert result is None

    def test_reset_clears_state(self):
        """Test that reset clears all storage state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = EventStorage(output_dir=temp_dir)
            storage.create_session_subfolder()
            storage.save_events(create_mock_events(5))

            assert storage.session_dir is not None
            assert storage.file_count == 1

            storage.reset()

            assert storage.session_dir is None
            assert storage.file_count == 0
            assert storage.total_events == 0


class TestPeriodicFlush:
    """Tests for periodic flush behavior (every few seconds)."""

    @pytest.mark.asyncio
    async def test_periodic_flush_creates_new_file_chunks(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that periodic flush creates new file chunks every few seconds."""
        from openhands.tools.browser_use.recording import RecordingConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create recording session with fast flush interval
            config = RecordingConfig(flush_interval_seconds=0.1)  # 100ms
            session = RecordingSession(config=config)
            session._storage._session_dir = temp_dir
            session._is_recording = True

            # Mock the CDP evaluate to return events on each flush
            flush_call_count = 0

            async def mock_evaluate(*args, **kwargs):
                nonlocal flush_call_count
                expression = kwargs.get("params", {}).get("expression", "")

                # Return events for flush calls
                if (
                    "window.__rrweb_events" in expression
                    and "JSON.stringify" in expression
                ):
                    flush_call_count += 1
                    events = create_mock_events(10)  # 10 events per flush
                    return {"result": {"value": json.dumps({"events": events})}}
                return {"result": {"value": None}}

            mock_cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock(
                side_effect=mock_evaluate
            )

            # Start the periodic flush task
            flush_task = asyncio.create_task(
                session._periodic_flush_loop(mock_browser_session)
            )

            # Let it run for enough time to create multiple flushes
            await asyncio.sleep(0.35)  # Should allow ~3 flush cycles

            # Stop recording to end the task
            session._is_recording = False
            await asyncio.sleep(0.15)  # Allow task to exit

            # Cancel if still running
            if not flush_task.done():
                flush_task.cancel()
                try:
                    await flush_task
                except asyncio.CancelledError:
                    pass

            # Verify: Multiple files should have been created
            files = sorted(os.listdir(temp_dir))
            json_files = [f for f in files if f.endswith(".json")]

            assert len(json_files) >= 2, (
                f"Expected at least 2 file chunks from periodic flush, "
                f"got {len(json_files)}: {json_files}"
            )

            # Verify each file contains valid events
            for json_file in json_files:
                filepath = os.path.join(temp_dir, json_file)
                with open(filepath) as f:
                    events = json.load(f)
                assert isinstance(events, list)
                assert len(events) > 0

    @pytest.mark.asyncio
    async def test_periodic_flush_interval_is_configurable(self):
        """Test that the flush interval constant is set correctly."""
        # Verify the default interval is 5 seconds
        assert RECORDING_FLUSH_INTERVAL_SECONDS == 5


class TestConcurrentFlushSafety:
    """Tests for concurrent flush safety (lock protection)."""

    @pytest.mark.asyncio
    async def test_concurrent_flushes_do_not_corrupt_event_buffer(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that concurrent flushes don't corrupt the event buffer."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = RecordingSession()
            session._storage._session_dir = temp_dir
            session._is_recording = True

            async def mock_evaluate(*args, **kwargs):
                expression = kwargs.get("params", {}).get("expression", "")
                if (
                    "window.__rrweb_events" in expression
                    and "JSON.stringify" in expression
                ):
                    events = create_mock_events(20, size_per_event=100)
                    return {"result": {"value": json.dumps({"events": events})}}
                return {"result": {"value": None}}

            mock_cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock(
                side_effect=mock_evaluate
            )

            # Trigger multiple concurrent flushes
            tasks = [
                asyncio.create_task(session.flush_events(mock_browser_session))
                for _ in range(5)
            ]
            await asyncio.gather(*tasks)

            # Verify: Events should be accumulated in buffer (5 flushes * 20 events)
            assert len(session.events) == 100

    @pytest.mark.asyncio
    async def test_periodic_flush_creates_timestamped_files(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that periodic flush creates timestamped files that are sortable."""
        from openhands.tools.browser_use.recording import RecordingConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RecordingConfig(flush_interval_seconds=0.05)
            session = RecordingSession(config=config)
            session._storage._session_dir = temp_dir
            session._is_recording = True

            async def mock_evaluate(*args, **kwargs):
                expression = kwargs.get("params", {}).get("expression", "")
                if (
                    "window.__rrweb_events" in expression
                    and "JSON.stringify" in expression
                ):
                    events = create_mock_events(20, size_per_event=100)
                    return {"result": {"value": json.dumps({"events": events})}}
                return {"result": {"value": None}}

            mock_cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock(
                side_effect=mock_evaluate
            )

            flush_task = asyncio.create_task(
                session._periodic_flush_loop(mock_browser_session)
            )
            await asyncio.sleep(0.2)

            session._is_recording = False
            await asyncio.sleep(0.1)
            if not flush_task.done():
                flush_task.cancel()
                try:
                    await flush_task
                except asyncio.CancelledError:
                    pass

            files = sorted(os.listdir(temp_dir))
            json_files = [f for f in files if f.endswith(".json")]

            # Files should be unique and sortable by timestamp
            assert len(json_files) >= 2, f"Expected at least 2 files, got {json_files}"
            assert len(json_files) == len(set(json_files)), "Files should be unique"

            # Verify file integrity
            for json_file in json_files:
                filepath = os.path.join(temp_dir, json_file)
                with open(filepath) as f:
                    events = json.load(f)
                assert isinstance(events, list)


class TestRecordingIsolation:
    """Tests for recording session isolation (separate subfolders)."""

    @pytest.mark.asyncio
    async def test_multiple_recordings_create_separate_subfolders(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that multiple start/stop cycles create separate subfolders."""
        import time

        with tempfile.TemporaryDirectory() as temp_dir:
            # Set up mock CDP session for successful recording
            # Note: stop_recording expects a JSON string, not a dict
            mock_cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock(
                side_effect=[
                    # First recording: wait for rrweb load
                    {"result": {"value": {"success": True}}},
                    # First recording: start recording
                    {"result": {"value": {"status": "started"}}},
                    # First recording: set recording flag (in stop)
                    {"result": {"value": None}},
                    # First recording: stop recording (returns JSON string)
                    {"result": {"value": json.dumps({"events": [{"type": 3}] * 5})}},
                    # First recording: set recording flag to false
                    {"result": {"value": None}},
                    # Second recording: wait for rrweb load
                    {"result": {"value": {"success": True}}},
                    # Second recording: start recording
                    {"result": {"value": {"status": "started"}}},
                    # Second recording: set recording flag (in stop)
                    {"result": {"value": None}},
                    # Second recording: stop recording (returns JSON string)
                    {"result": {"value": json.dumps({"events": [{"type": 3}] * 10})}},
                    # Second recording: set recording flag to false
                    {"result": {"value": None}},
                ]
            )
            mock_cdp_session.cdp_client.send.Page.addScriptToEvaluateOnNewDocument = (
                AsyncMock(return_value={"identifier": "script-1"})
            )

            # First recording session
            session1 = RecordingSession(output_dir=temp_dir)
            await session1.start(mock_browser_session)
            session_dir_1 = session1.session_dir
            await session1.stop(mock_browser_session)

            # Small delay to ensure different timestamps
            time.sleep(0.01)

            # Second recording session
            session2 = RecordingSession(output_dir=temp_dir)
            await session2.start(mock_browser_session)
            session_dir_2 = session2.session_dir
            await session2.stop(mock_browser_session)

            # Verify: Two separate subfolders were created
            subdirs = [
                d
                for d in os.listdir(temp_dir)
                if os.path.isdir(os.path.join(temp_dir, d))
            ]
            assert len(subdirs) == 2, (
                f"Expected 2 recording subfolders, got {len(subdirs)}: {subdirs}"
            )

            # Verify both start with "recording-"
            for subdir in subdirs:
                assert subdir.startswith("recording-"), (
                    f"Expected subfolder to start with 'recording-', got {subdir}"
                )

            # Verify the session_dirs are different
            assert session_dir_1 != session_dir_2, (
                "Expected different session directories for each recording"
            )

            # Verify each subfolder has its own files
            for subdir in subdirs:
                subdir_path = os.path.join(temp_dir, subdir)
                files = os.listdir(subdir_path)
                json_files = [f for f in files if f.endswith(".json")]
                assert len(json_files) > 0, (
                    f"Expected at least one JSON file in {subdir}"
                )


class TestFileCountAccuracy:
    """Tests for accurate file count reporting."""

    @pytest.mark.asyncio
    async def test_file_count_accurate_with_existing_files(self):
        """Test that file count is accurate when session_dir has existing files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Pre-create some files to simulate existing recordings
            for i in range(1, 4):  # Create 1.json, 2.json, 3.json
                with open(os.path.join(temp_dir, f"{i}.json"), "w") as f:
                    json.dump([{"type": "existing"}], f)

            session = RecordingSession()
            session._storage._session_dir = temp_dir
            session._is_recording = True

            # Add events to buffer and save twice
            for _ in range(2):
                session._events.extend(create_mock_events(20))
                session._save_and_clear_events()

            # Verify: file_count should be 2 (files written this session)
            assert session.file_count == 2, (
                f"Expected file_count=2 (files written), got {session.file_count}"
            )

            # Verify new files were created (timestamps, not numbered)
            files = sorted(os.listdir(temp_dir))
            json_files = [f for f in files if f.endswith(".json")]
            assert len(json_files) == 5  # 3 existing + 2 new

    @pytest.mark.asyncio
    async def test_file_count_zero_when_no_events(self):
        """Test that file count is 0 when no events are recorded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = RecordingSession()
            session._storage._session_dir = temp_dir
            session._is_recording = True

            # No flush calls, no events
            assert session.file_count == 0

    @pytest.mark.asyncio
    async def test_file_count_matches_actual_files_written(self):
        """Test that file_count exactly matches number of files written."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = RecordingSession()
            session._storage._session_dir = temp_dir
            session._is_recording = True

            # Add events to buffer and save 5 times
            for _ in range(5):
                session._events.extend(create_mock_events(20))
                session._save_and_clear_events()

            # Verify file_count matches actual files
            files = os.listdir(temp_dir)
            json_files = [f for f in files if f.endswith(".json")]
            assert session.file_count == len(json_files) == 5
