"""Tests for output deduplication in remote workspace polling.

These tests verify that the polling loop in RemoteWorkspaceMixin correctly
fetches only new events using order__gt filtering.

Bug context:
- Previously, the bash events search API returned ALL events on each call
- Without filtering, output got duplicated: A + B + A + B + C + ...
- This caused base64 decoding failures in trajectory capture

Fix:
- Client now passes order__gt parameter to fetch only new events
- API filters events with order > last_order

Error messages that were observed in production:
- "Invalid base64-encoded string: number of data characters (5352925)
   cannot be 1 more than a multiple of 4"
- "Incorrect padding"
"""

import base64
from unittest.mock import Mock, patch

import pytest

from openhands.sdk.workspace.remote.remote_workspace_mixin import RemoteWorkspaceMixin


class RemoteWorkspaceMixinHelper(RemoteWorkspaceMixin):
    """Test implementation of RemoteWorkspaceMixin for testing purposes."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class TestPollingDeduplication:
    """Tests for proper event filtering using order__gt in the polling loop."""

    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_polling_should_not_duplicate_events_across_iterations(self, mock_time):
        """Test that polling uses order__gt to fetch only new events.

        When a command produces output over multiple poll iterations,
        the client should use order__gt to request only events newer than
        the last one it processed.

        Expected correct output: chunk1 + chunk2 + chunk3
        """
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        mock_time.time.side_effect = [0, 1, 2, 3, 4]
        mock_time.sleep = Mock()

        start_response = Mock()
        start_response.raise_for_status = Mock()
        start_response.json.return_value = {"id": "cmd-123"}

        # Poll 1: First poll (no order__gt), returns chunk 1
        poll_response_1 = Mock()
        poll_response_1.raise_for_status = Mock()
        poll_response_1.json.return_value = {
            "items": [
                {
                    "id": "event-1",
                    "kind": "BashOutput",
                    "order": 0,
                    "stdout": "CHUNK1",
                    "stderr": None,
                    "exit_code": None,
                },
            ]
        }

        # Poll 2: With order__gt=0, API returns only chunk 2
        poll_response_2 = Mock()
        poll_response_2.raise_for_status = Mock()
        poll_response_2.json.return_value = {
            "items": [
                {
                    "id": "event-2",
                    "kind": "BashOutput",
                    "order": 1,
                    "stdout": "CHUNK2",
                    "stderr": None,
                    "exit_code": None,
                },
            ]
        }

        # Poll 3: With order__gt=1, API returns only chunk 3
        poll_response_3 = Mock()
        poll_response_3.raise_for_status = Mock()
        poll_response_3.json.return_value = {
            "items": [
                {
                    "id": "event-3",
                    "kind": "BashOutput",
                    "order": 2,
                    "stdout": "CHUNK3",
                    "stderr": None,
                    "exit_code": 0,
                },
            ]
        }

        generator = mixin._execute_command_generator("test_command", None, 30.0)

        next(generator)
        generator.send(start_response)
        generator.send(poll_response_1)
        generator.send(poll_response_2)

        try:
            generator.send(poll_response_3)
            pytest.fail("Generator should have stopped")
        except StopIteration as e:
            result = e.value

        # Output should be exactly the 3 chunks with NO duplication
        assert result.stdout == "CHUNK1CHUNK2CHUNK3", (
            f"Expected 'CHUNK1CHUNK2CHUNK3' but got '{result.stdout}'. "
            "Events should be deduplicated across poll iterations."
        )

    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_base64_output_should_decode_correctly(self, mock_time):
        """Test that base64 output is not corrupted by polling.

        This test verifies the fix for production errors:
        - "Incorrect padding"
        - "Invalid base64-encoded string"

        The trajectory capture runs: tar -czf - workspace | base64
        Then decodes with base64.b64decode(stdout)

        With order__gt filtering, each poll returns only new events.
        """
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        mock_time.time.side_effect = [0, 1, 2, 3, 4]
        mock_time.sleep = Mock()

        # Create base64 data simulating tar output
        original_data = b"Test data!" * 5
        base64_encoded = base64.b64encode(original_data).decode("ascii")

        # Split into chunks (simulating chunked transmission)
        chunk1 = base64_encoded[:17]
        chunk2 = base64_encoded[17:34]
        chunk3 = base64_encoded[34:]

        start_response = Mock()
        start_response.raise_for_status = Mock()
        start_response.json.return_value = {"id": "cmd-456"}

        # Poll 1: First poll, returns chunk 1
        poll_response_1 = Mock()
        poll_response_1.raise_for_status = Mock()
        poll_response_1.json.return_value = {
            "items": [
                {
                    "id": "event-1",
                    "kind": "BashOutput",
                    "order": 0,
                    "stdout": chunk1,
                    "stderr": None,
                    "exit_code": None,
                },
            ]
        }

        # Poll 2: With order__gt=0, API returns only chunk 2
        poll_response_2 = Mock()
        poll_response_2.raise_for_status = Mock()
        poll_response_2.json.return_value = {
            "items": [
                {
                    "id": "event-2",
                    "kind": "BashOutput",
                    "order": 1,
                    "stdout": chunk2,
                    "stderr": None,
                    "exit_code": None,
                },
            ]
        }

        # Poll 3: With order__gt=1, API returns only chunk 3
        poll_response_3 = Mock()
        poll_response_3.raise_for_status = Mock()
        poll_response_3.json.return_value = {
            "items": [
                {
                    "id": "event-3",
                    "kind": "BashOutput",
                    "order": 2,
                    "stdout": chunk3,
                    "stderr": None,
                    "exit_code": 0,
                },
            ]
        }

        generator = mixin._execute_command_generator(
            "tar -czf - workspace | base64", None, 30.0
        )

        next(generator)
        generator.send(start_response)
        generator.send(poll_response_1)
        generator.send(poll_response_2)

        try:
            generator.send(poll_response_3)
            pytest.fail("Generator should have stopped")
        except StopIteration as e:
            result = e.value

        # Output should be valid base64 that decodes correctly
        assert result.stdout == base64_encoded, (
            f"Expected valid base64 '{base64_encoded}' but got '{result.stdout}'. "
            "Output should not be corrupted by duplicate events."
        )

        # Verify it actually decodes
        decoded = base64.b64decode(result.stdout)
        assert decoded == original_data

    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_base64_decode_succeeds_with_order_filtering(self, mock_time):
        """Test that base64 decoding works correctly with order__gt filtering.

        This test verifies that the order__gt fix prevents the error that was
        seen in production logs:
        - "Incorrect padding" error from base64.b64decode()

        The trajectory capture code runs:
            tar -czf - workspace | base64
        Then decodes with:
            base64.b64decode(stdout)

        With order__gt filtering, output is not duplicated and decodes correctly.
        """
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        mock_time.time.side_effect = [0, 1, 2, 3, 4]
        mock_time.sleep = Mock()

        # Create base64 data
        original_data = b"Test data!" * 5
        base64_encoded = base64.b64encode(original_data).decode("ascii")

        chunk1 = base64_encoded[:17]  # 17 chars
        chunk2 = base64_encoded[17:34]  # 17 chars
        chunk3 = base64_encoded[34:]  # 34 chars

        start_response = Mock()
        start_response.raise_for_status = Mock()
        start_response.json.return_value = {"id": "cmd-789"}

        # Poll 1: First poll, returns chunk 1
        poll_response_1 = Mock()
        poll_response_1.raise_for_status = Mock()
        poll_response_1.json.return_value = {
            "items": [
                {
                    "id": "event-1",
                    "kind": "BashOutput",
                    "order": 0,
                    "stdout": chunk1,
                    "stderr": None,
                    "exit_code": None,
                },
            ]
        }

        # Poll 2: With order__gt=0, API returns only chunk 2
        poll_response_2 = Mock()
        poll_response_2.raise_for_status = Mock()
        poll_response_2.json.return_value = {
            "items": [
                {
                    "id": "event-2",
                    "kind": "BashOutput",
                    "order": 1,
                    "stdout": chunk2,
                    "stderr": None,
                    "exit_code": None,
                },
            ]
        }

        # Poll 3: With order__gt=1, API returns only chunk 3
        poll_response_3 = Mock()
        poll_response_3.raise_for_status = Mock()
        poll_response_3.json.return_value = {
            "items": [
                {
                    "id": "event-3",
                    "kind": "BashOutput",
                    "order": 2,
                    "stdout": chunk3,
                    "stderr": None,
                    "exit_code": 0,
                },
            ]
        }

        generator = mixin._execute_command_generator(
            "tar -czf - workspace | base64", None, 30.0
        )

        next(generator)
        generator.send(start_response)
        generator.send(poll_response_1)
        generator.send(poll_response_2)

        try:
            generator.send(poll_response_3)
            pytest.fail("Generator should have stopped")
        except StopIteration as e:
            result = e.value

        # Output should be valid base64 (68 chars, 68 % 4 = 0)
        assert result.stdout == base64_encoded, (
            f"Expected '{base64_encoded}' but got '{result.stdout}'"
        )

        # Decode should succeed (this would fail with "Incorrect padding" before fix)
        decoded = base64.b64decode(result.stdout)
        assert decoded == original_data, (
            f"base64.b64decode() should succeed and return original data. "
            f"Got {len(result.stdout)} chars (length % 4 = {len(result.stdout) % 4})"
        )

    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_assertion_fires_on_duplicate_events(self, mock_time):
        """Test that an AssertionError is raised if duplicate events are received.

        This is a safety check - the API should filter duplicates via order__gt,
        but if it doesn't, the client should detect and fail fast rather than
        silently corrupting output.
        """
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        mock_time.time.side_effect = [0, 1, 2, 3]
        mock_time.sleep = Mock()

        start_response = Mock()
        start_response.raise_for_status = Mock()
        start_response.json.return_value = {"id": "cmd-999"}

        # Poll 1: Returns event-1
        poll_response_1 = Mock()
        poll_response_1.raise_for_status = Mock()
        poll_response_1.json.return_value = {
            "items": [
                {
                    "id": "event-1",
                    "kind": "BashOutput",
                    "order": 0,
                    "stdout": "CHUNK1",
                    "stderr": None,
                    "exit_code": None,
                },
            ]
        }

        # Poll 2: API bug - returns event-1 again (duplicate!)
        poll_response_2 = Mock()
        poll_response_2.raise_for_status = Mock()
        poll_response_2.json.return_value = {
            "items": [
                {
                    "id": "event-1",  # Duplicate!
                    "kind": "BashOutput",
                    "order": 0,
                    "stdout": "CHUNK1",
                    "stderr": None,
                    "exit_code": None,
                },
                {
                    "id": "event-2",
                    "kind": "BashOutput",
                    "order": 1,
                    "stdout": "CHUNK2",
                    "stderr": None,
                    "exit_code": 0,
                },
            ]
        }

        generator = mixin._execute_command_generator("test_command", None, 30.0)

        next(generator)
        generator.send(start_response)
        generator.send(poll_response_1)

        # The assertion is caught and returns an error result
        try:
            generator.send(poll_response_2)
            pytest.fail("Generator should have stopped")
        except StopIteration as e:
            result = e.value

        # Should return error result with duplicate event message
        assert result.exit_code == -1
        assert "Duplicate event received: event-1" in result.stderr

    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_single_poll_works_correctly(self, mock_time):
        """Test that single poll iteration works correctly.

        When a command completes within a single poll, there's no
        opportunity for duplication. This should always work.
        """
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        mock_time.time.side_effect = [0, 1]
        mock_time.sleep = Mock()

        start_response = Mock()
        start_response.raise_for_status = Mock()
        start_response.json.return_value = {"id": "cmd-789"}

        # Single poll returns all events with exit code
        poll_response = Mock()
        poll_response.raise_for_status = Mock()
        poll_response.json.return_value = {
            "items": [
                {
                    "id": "event-1",
                    "kind": "BashOutput",
                    "order": 0,
                    "stdout": "CHUNK1",
                    "stderr": None,
                    "exit_code": None,
                },
                {
                    "id": "event-2",
                    "kind": "BashOutput",
                    "order": 1,
                    "stdout": "CHUNK2",
                    "stderr": None,
                    "exit_code": None,
                },
                {
                    "id": "event-3",
                    "kind": "BashOutput",
                    "order": 2,
                    "stdout": "CHUNK3",
                    "stderr": None,
                    "exit_code": 0,
                },
            ]
        }

        generator = mixin._execute_command_generator("fast_command", None, 30.0)

        next(generator)
        generator.send(start_response)

        try:
            generator.send(poll_response)
            pytest.fail("Generator should have stopped")
        except StopIteration as e:
            result = e.value

        assert result.stdout == "CHUNK1CHUNK2CHUNK3"
