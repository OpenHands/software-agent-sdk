"""Tests for browser session recording flush behavior.

These tests verify that:
1. Recording events are periodically flushed to new file chunks
2. Events are flushed to a new file when size threshold is exceeded
"""

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.tools.browser_use.server import (
    RECORDING_FLUSH_INTERVAL_SECONDS,
    RECORDING_FLUSH_SIZE_MB,
    CustomBrowserUseServer,
)


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
        self, server_with_mock_browser, mock_cdp_session
    ):
        """Test that periodic flush creates new file chunks every few seconds."""
        server = server_with_mock_browser

        with tempfile.TemporaryDirectory() as temp_dir:
            # Setup: Configure server for recording
            server._is_recording = True
            server._recording_save_dir = temp_dir
            server._recording_file_counter = 0
            server._recording_events = []

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

            # Run periodic flush task for a short time with reduced interval
            # We'll patch the interval to make the test faster
            with patch(
                "openhands.tools.browser_use.server.RECORDING_FLUSH_INTERVAL_SECONDS",
                0.1,  # 100ms instead of 5 seconds
            ):
                # Start the periodic flush task
                flush_task = asyncio.create_task(server._periodic_flush_task())

                # Let it run for enough time to create multiple flushes
                await asyncio.sleep(0.35)  # Should allow ~3 flush cycles

                # Stop recording to end the task
                server._is_recording = False
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
        self, server_with_mock_browser, mock_cdp_session
    ):
        """Test that events are flushed to a new file when size threshold is exceeded."""
        server = server_with_mock_browser

        with tempfile.TemporaryDirectory() as temp_dir:
            # Setup: Configure server for recording
            server._is_recording = True
            server._recording_save_dir = temp_dir
            server._recording_file_counter = 0
            server._recording_events = []

            # Create events that exceed the size threshold
            # RECORDING_FLUSH_SIZE_MB is 1 MB, so we need > 1MB of events
            # Each event is roughly 100 bytes, so we need > 10,000 events
            # But for testing, we'll patch the threshold to be smaller
            with patch(
                "openhands.tools.browser_use.server.RECORDING_FLUSH_SIZE_MB",
                0.001,  # 1 KB threshold for testing
            ):
                # Mock CDP to return large batch of events
                large_events = create_mock_events(50, size_per_event=100)  # ~5KB

                async def mock_evaluate(*args, **kwargs):
                    expression = kwargs.get("params", {}).get("expression", "")
                    if (
                        "window.__rrweb_events" in expression
                        and "JSON.stringify" in expression
                    ):
                        return {
                            "result": {"value": json.dumps({"events": large_events})}
                        }
                    return {"result": {"value": None}}

                mock_cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock(
                    side_effect=mock_evaluate
                )

                # Call flush - this should trigger size-based save
                await server._flush_recording_events()

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
            assert len(server._recording_events) == 0

    @pytest.mark.asyncio
    async def test_no_flush_when_below_size_threshold(
        self, server_with_mock_browser, mock_cdp_session
    ):
        """Test that events are NOT flushed when below size threshold."""
        server = server_with_mock_browser

        with tempfile.TemporaryDirectory() as temp_dir:
            # Setup: Configure server for recording
            server._is_recording = True
            server._recording_save_dir = temp_dir
            server._recording_file_counter = 0
            server._recording_events = []

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
            await server._flush_recording_events()

            # Verify: No file should have been created (below threshold)
            files = os.listdir(temp_dir)
            json_files = [f for f in files if f.endswith(".json")]

            assert len(json_files) == 0, (
                f"Expected no files (below threshold), got {len(json_files)}"
            )

            # Events should still be in memory
            assert len(server._recording_events) == 5

    @pytest.mark.asyncio
    async def test_size_threshold_is_configurable(self):
        """Test that the size threshold constant is set correctly."""
        # Verify the default threshold is 1 MB
        assert RECORDING_FLUSH_SIZE_MB == 1

    @pytest.mark.asyncio
    async def test_multiple_flushes_create_sequential_files(
        self, server_with_mock_browser, mock_cdp_session
    ):
        """Test that multiple size-triggered flushes create sequentially numbered files."""
        server = server_with_mock_browser

        with tempfile.TemporaryDirectory() as temp_dir:
            # Setup
            server._is_recording = True
            server._recording_save_dir = temp_dir
            server._recording_file_counter = 0
            server._recording_events = []

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

            # Patch threshold to be very small
            with patch(
                "openhands.tools.browser_use.server.RECORDING_FLUSH_SIZE_MB",
                0.001,  # 1 KB threshold
            ):
                # Trigger multiple flushes
                for _ in range(3):
                    await server._flush_recording_events()

            # Verify: 3 sequentially numbered files should exist
            files = sorted(os.listdir(temp_dir))
            json_files = [f for f in files if f.endswith(".json")]

            assert len(json_files) == 3
            assert json_files == ["1.json", "2.json", "3.json"]
