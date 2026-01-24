"""Tests for output deduplication in remote workspace polling.

These tests verify that the polling loop in RemoteWorkspaceMixin correctly
deduplicates events when the API returns all events on each poll iteration.

Bug context:
- The bash events search API returns ALL events from the beginning on each call
- Without deduplication, output gets duplicated: A + B + A + B + C + ...
- This causes base64 decoding failures in trajectory capture

Error messages observed in production:
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
    """Tests for proper event deduplication in the polling loop."""

    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_polling_should_not_duplicate_events_across_iterations(self, mock_time):
        """Test that polling deduplicates events returned by the API.

        When a command produces output over multiple poll iterations,
        the API returns ALL events on each poll. The implementation must
        deduplicate to avoid output like:
        chunk1 + chunk1 + chunk2 + chunk1 + chunk2 + chunk3

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

        # Poll 1: Only chunk 1 exists
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

        # Poll 2: API returns ALL events (chunks 1 and 2)
        poll_response_2 = Mock()
        poll_response_2.raise_for_status = Mock()
        poll_response_2.json.return_value = {
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
            ]
        }

        # Poll 3: API returns ALL events (chunks 1, 2, and 3)
        poll_response_3 = Mock()
        poll_response_3.raise_for_status = Mock()
        poll_response_3.json.return_value = {
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

        Without deduplication, the output becomes invalid base64.
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

        # Poll 1: Only chunk 1
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

        # Poll 2: API returns ALL events
        poll_response_2 = Mock()
        poll_response_2.raise_for_status = Mock()
        poll_response_2.json.return_value = {
            "items": [
                {
                    "id": "event-1",
                    "kind": "BashOutput",
                    "order": 0,
                    "stdout": chunk1,
                    "stderr": None,
                    "exit_code": None,
                },
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

        # Poll 3: API returns ALL events
        poll_response_3 = Mock()
        poll_response_3.raise_for_status = Mock()
        poll_response_3.json.return_value = {
            "items": [
                {
                    "id": "event-1",
                    "kind": "BashOutput",
                    "order": 0,
                    "stdout": chunk1,
                    "stderr": None,
                    "exit_code": None,
                },
                {
                    "id": "event-2",
                    "kind": "BashOutput",
                    "order": 1,
                    "stdout": chunk2,
                    "stderr": None,
                    "exit_code": None,
                },
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
    def test_base64_decode_produces_incorrect_padding_error(self, mock_time):
        """Test that reproduces the exact error seen in production logs.

        This test demonstrates that the duplicated output causes:
        - "Incorrect padding" error from base64.b64decode()

        The trajectory capture code runs:
            tar -czf - workspace | base64
        Then decodes with:
            base64.b64decode(stdout)

        When chunks are duplicated, the total length is no longer a multiple
        of 4, causing the decode to fail.
        """
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        mock_time.time.side_effect = [0, 1, 2, 3, 4]
        mock_time.sleep = Mock()

        # Create base64 data with chunk sizes that produce invalid length
        # when duplicated:
        # Original: 68 chars (valid, 68 % 4 = 0)
        # Duplicated: 17+17+17+17+17+34 = 119 chars (INVALID, 119 % 4 = 3)
        original_data = b"Test data!" * 5
        base64_encoded = base64.b64encode(original_data).decode("ascii")

        chunk1 = base64_encoded[:17]  # 17 chars
        chunk2 = base64_encoded[17:34]  # 17 chars
        chunk3 = base64_encoded[34:]  # 34 chars

        start_response = Mock()
        start_response.raise_for_status = Mock()
        start_response.json.return_value = {"id": "cmd-789"}

        # Poll 1: chunk 1
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

        # Poll 2: API returns ALL events
        poll_response_2 = Mock()
        poll_response_2.raise_for_status = Mock()
        poll_response_2.json.return_value = {
            "items": [
                {
                    "id": "event-1",
                    "kind": "BashOutput",
                    "order": 0,
                    "stdout": chunk1,
                    "stderr": None,
                    "exit_code": None,
                },
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

        # Poll 3: API returns ALL events, command completes
        poll_response_3 = Mock()
        poll_response_3.raise_for_status = Mock()
        poll_response_3.json.return_value = {
            "items": [
                {
                    "id": "event-1",
                    "kind": "BashOutput",
                    "order": 0,
                    "stdout": chunk1,
                    "stderr": None,
                    "exit_code": None,
                },
                {
                    "id": "event-2",
                    "kind": "BashOutput",
                    "order": 1,
                    "stdout": chunk2,
                    "stderr": None,
                    "exit_code": None,
                },
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

        # Attempt to decode the output - this is what trajectory capture does
        # This should NOT raise an error if deduplication is working correctly
        decoded = base64.b64decode(result.stdout)
        assert decoded == original_data, (
            f"base64.b64decode() should succeed and return original data. "
            f"Got {len(result.stdout)} chars (length % 4 = {len(result.stdout) % 4})"
        )

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
