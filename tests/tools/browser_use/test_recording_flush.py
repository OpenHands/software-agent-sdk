"""Tests for browser session recording flush behavior.

These tests verify that:
1. Recording events are periodically flushed to new file chunks
2. Events are flushed to a new file when size threshold is exceeded
"""

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from openhands.tools.browser_use.recording import (
    DEFAULT_CONFIG,
    RecordingSession,
    RecordingState,
)
from openhands.tools.browser_use.server import CustomBrowserUseServer


# Get default config values for tests
RECORDING_FLUSH_INTERVAL_SECONDS = DEFAULT_CONFIG.flush_interval_seconds
RECORDING_FLUSH_SIZE_MB = DEFAULT_CONFIG.flush_size_mb


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
            session = RecordingSession(save_dir=temp_dir, config=config)
            session._state = RecordingState.RECORDING

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
            session._state = RecordingState.IDLE
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


class TestSizeThresholdFlush:
    """Tests for size threshold flush behavior (when events exceed MB limit)."""

    @pytest.mark.asyncio
    async def test_flush_creates_new_file_when_size_threshold_exceeded(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that events are flushed to a new file when size
        threshold is exceeded."""
        from openhands.tools.browser_use.recording import RecordingConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create recording session with small size threshold
            config = RecordingConfig(flush_size_mb=0.001)  # 1 KB threshold
            session = RecordingSession(save_dir=temp_dir, config=config)
            session._state = RecordingState.RECORDING

            # Mock CDP to return large batch of events
            large_events = create_mock_events(50, size_per_event=100)  # ~5KB

            async def mock_evaluate(*args, **kwargs):
                expression = kwargs.get("params", {}).get("expression", "")
                if (
                    "window.__rrweb_events" in expression
                    and "JSON.stringify" in expression
                ):
                    return {"result": {"value": json.dumps({"events": large_events})}}
                return {"result": {"value": None}}

            mock_cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock(
                side_effect=mock_evaluate
            )

            # Call flush - this should trigger size-based save
            await session.flush_events(mock_browser_session)

            # Verify: A file should have been created due to size threshold
            files = os.listdir(temp_dir)
            json_files = [f for f in files if f.endswith(".json")]

            assert len(json_files) == 1, (
                f"Expected 1 file from size threshold flush, got {len(json_files)}"
            )

            # Verify the file contains the events
            filepath = os.path.join(temp_dir, json_files[0])
            with open(filepath) as f:
                saved_events = json.load(f)
            assert len(saved_events) == 50

            # Verify internal state was cleared after save
            assert len(session.event_buffer) == 0

    @pytest.mark.asyncio
    async def test_no_flush_when_below_size_threshold(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that events are NOT flushed when below size threshold."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create recording session with default 1MB threshold
            session = RecordingSession(save_dir=temp_dir)
            session._state = RecordingState.RECORDING

            # Create small batch of events (well below 1MB threshold)
            small_events = create_mock_events(5, size_per_event=100)  # ~500 bytes

            async def mock_evaluate(*args, **kwargs):
                expression = kwargs.get("params", {}).get("expression", "")
                if (
                    "window.__rrweb_events" in expression
                    and "JSON.stringify" in expression
                ):
                    return {"result": {"value": json.dumps({"events": small_events})}}
                return {"result": {"value": None}}

            mock_cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock(
                side_effect=mock_evaluate
            )

            # Call flush - this should NOT trigger size-based save
            await session.flush_events(mock_browser_session)

            # Verify: No file should have been created (below threshold)
            files = os.listdir(temp_dir)
            json_files = [f for f in files if f.endswith(".json")]

            assert len(json_files) == 0, (
                f"Expected no files (below threshold), got {len(json_files)}"
            )

            # Events should still be in memory
            assert len(session.event_buffer) == 5

    @pytest.mark.asyncio
    async def test_size_threshold_is_configurable(self):
        """Test that the size threshold constant is set correctly."""
        # Verify the default threshold is 1 MB
        assert RECORDING_FLUSH_SIZE_MB == 1

    @pytest.mark.asyncio
    async def test_multiple_flushes_create_sequential_files(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that multiple size-triggered flushes
        create sequentially numbered files."""
        from openhands.tools.browser_use.recording import RecordingConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create recording session with small size threshold
            config = RecordingConfig(flush_size_mb=0.001)  # 1 KB threshold
            session = RecordingSession(save_dir=temp_dir, config=config)
            session._state = RecordingState.RECORDING

            flush_count = 0

            async def mock_evaluate(*args, **kwargs):
                nonlocal flush_count
                expression = kwargs.get("params", {}).get("expression", "")
                if (
                    "window.__rrweb_events" in expression
                    and "JSON.stringify" in expression
                ):
                    flush_count += 1
                    events = create_mock_events(20, size_per_event=100)
                    return {"result": {"value": json.dumps({"events": events})}}
                return {"result": {"value": None}}

            mock_cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock(
                side_effect=mock_evaluate
            )

            # Trigger multiple flushes
            for _ in range(3):
                await session.flush_events(mock_browser_session)

            # Verify: 3 sequentially numbered files should exist
            files = sorted(os.listdir(temp_dir))
            json_files = [f for f in files if f.endswith(".json")]

            assert len(json_files) == 3
            assert json_files == ["1.json", "2.json", "3.json"]


class TestConcurrentFlushSafety:
    """Tests for concurrent flush safety (lock protection)."""

    @pytest.mark.asyncio
    async def test_concurrent_flushes_do_not_corrupt_file_counter(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that concurrent flushes don't cause file counter races."""
        from openhands.tools.browser_use.recording import RecordingConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create recording session with small size threshold
            config = RecordingConfig(flush_size_mb=0.001)  # 1 KB threshold
            session = RecordingSession(save_dir=temp_dir, config=config)
            session._state = RecordingState.RECORDING

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

            # Verify: Files should be sequentially numbered without gaps/duplicates
            files = sorted(os.listdir(temp_dir))
            json_files = [f for f in files if f.endswith(".json")]

            # All files should exist with sequential numbering
            expected_files = [f"{i}.json" for i in range(1, len(json_files) + 1)]
            assert json_files == expected_files, (
                f"Expected sequential files {expected_files}, got {json_files}"
            )

            # Each file should contain valid JSON and not be corrupted
            for json_file in json_files:
                filepath = os.path.join(temp_dir, json_file)
                with open(filepath) as f:
                    events = json.load(f)
                assert isinstance(events, list)
                assert len(events) > 0

    @pytest.mark.asyncio
    async def test_periodic_and_navigation_flush_do_not_race(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that periodic flush and navigation-triggered flush coordinate."""
        from openhands.tools.browser_use.recording import RecordingConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            # Very fast flush interval to increase chance of race
            config = RecordingConfig(flush_interval_seconds=0.05, flush_size_mb=0.001)
            session = RecordingSession(save_dir=temp_dir, config=config)
            session._state = RecordingState.RECORDING

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

            # Start periodic flush
            flush_task = asyncio.create_task(
                session._periodic_flush_loop(mock_browser_session)
            )

            # Simulate navigation-triggered flushes concurrently
            for _ in range(3):
                await session.flush_events(mock_browser_session)
                await asyncio.sleep(0.02)

            # Stop and cleanup
            session._state = RecordingState.IDLE
            await asyncio.sleep(0.1)
            if not flush_task.done():
                flush_task.cancel()
                try:
                    await flush_task
                except asyncio.CancelledError:
                    pass

            # Verify: No file corruption or duplicate file numbers
            files = sorted(os.listdir(temp_dir))
            json_files = [f for f in files if f.endswith(".json")]

            # Files should be sequentially numbered
            expected_files = [f"{i}.json" for i in range(1, len(json_files) + 1)]
            assert json_files == expected_files, (
                f"Expected sequential files {expected_files}, got {json_files}"
            )

            # Verify file integrity
            for json_file in json_files:
                filepath = os.path.join(temp_dir, json_file)
                with open(filepath) as f:
                    events = json.load(f)
                assert isinstance(events, list)


class TestFileCountAccuracy:
    """Tests for accurate file count reporting."""

    @pytest.mark.asyncio
    async def test_file_count_accurate_with_existing_files(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that file count is accurate when save_dir has existing files."""
        from openhands.tools.browser_use.recording import RecordingConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            # Pre-create some files to simulate existing recordings
            for i in range(1, 4):  # Create 1.json, 2.json, 3.json
                with open(os.path.join(temp_dir, f"{i}.json"), "w") as f:
                    json.dump([{"type": "existing"}], f)

            # Create recording session with small size threshold
            config = RecordingConfig(flush_size_mb=0.001)  # 1 KB threshold
            session = RecordingSession(save_dir=temp_dir, config=config)
            session._state = RecordingState.RECORDING

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

            # Trigger multiple flushes
            for _ in range(2):
                await session.flush_events(mock_browser_session)

            # Verify: file_count should be 2 (files written), not 5 (last index)
            assert session.file_count == 2, (
                f"Expected file_count=2 (files written), got {session.file_count}"
            )

            # Verify the new files are 4.json and 5.json (skipping existing 1-3)
            files = sorted(os.listdir(temp_dir))
            json_files = [f for f in files if f.endswith(".json")]
            assert "4.json" in json_files
            assert "5.json" in json_files
            assert len(json_files) == 5  # 3 existing + 2 new

    @pytest.mark.asyncio
    async def test_file_count_zero_when_no_events(self):
        """Test that file count is 0 when no events are recorded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = RecordingSession(save_dir=temp_dir)
            session._state = RecordingState.RECORDING

            # No flush calls, no events
            assert session.file_count == 0

    @pytest.mark.asyncio
    async def test_file_count_matches_actual_files_written(
        self, mock_browser_session, mock_cdp_session
    ):
        """Test that file_count exactly matches number of files written."""
        from openhands.tools.browser_use.recording import RecordingConfig

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RecordingConfig(flush_size_mb=0.001)  # 1 KB threshold
            session = RecordingSession(save_dir=temp_dir, config=config)
            session._state = RecordingState.RECORDING

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

            # Trigger exactly 5 flushes
            for _ in range(5):
                await session.flush_events(mock_browser_session)

            # Verify file_count matches actual files
            files = os.listdir(temp_dir)
            json_files = [f for f in files if f.endswith(".json")]
            assert session.file_count == len(json_files) == 5
