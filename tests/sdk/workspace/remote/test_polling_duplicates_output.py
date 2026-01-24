"""Tests for output duplication bug in remote workspace polling.

This test file documents and reproduces a bug where the polling loop in
RemoteWorkspaceMixin._execute_command_generator() duplicates output when
multiple poll iterations occur before command completion.

The bug manifests as base64 decoding failures when capturing conversation
trajectories, because the duplicated output produces invalid base64.

Bug details:
- The polling loop fetches ALL events from the beginning on each iteration
- Events are appended to stdout_parts/stderr_parts without deduplication
- This causes output like: A + B + A + B + C + A + B + C + D
- For base64-encoded data, this corruption causes decode failures

Error messages observed in production:
- "Invalid base64-encoded string: number of data characters (5352925)
   cannot be 1 more than a multiple of 4"
- "Incorrect padding"

See: https://github.com/All-Hands-AI/OpenHands/issues/XXXX
"""

import base64
from unittest.mock import Mock, patch

import pytest

from openhands.sdk.workspace.remote.remote_workspace_mixin import RemoteWorkspaceMixin


class RemoteWorkspaceMixinHelper(RemoteWorkspaceMixin):
    """Test implementation of RemoteWorkspaceMixin for testing purposes."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class TestPollingDuplicatesOutput:
    """Test class for the output duplication bug in polling loop."""

    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_polling_loop_duplicates_events_across_iterations(self, mock_time):
        """Test that demonstrates the polling loop duplication bug.

        When a command produces output over multiple poll iterations,
        the current implementation re-fetches ALL events on each poll
        and appends them again, causing massive output duplication.

        This test simulates a command that produces 3 chunks of output
        across 3 poll iterations. The bug causes the output to be:
        chunk1 + chunk1 + chunk2 + chunk1 + chunk2 + chunk3
        instead of the correct:
        chunk1 + chunk2 + chunk3
        """
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        # Mock time to allow 3 poll iterations before timeout
        mock_time.time.side_effect = [0, 1, 2, 3, 4]
        mock_time.sleep = Mock()

        # Mock start response
        start_response = Mock()
        start_response.raise_for_status = Mock()
        start_response.json.return_value = {"id": "cmd-123"}

        # Simulate 3 poll iterations with accumulating events
        # Each poll returns ALL events seen so far (as the real API does)

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

        # Poll 2: Chunks 1 and 2 exist
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

        # Poll 3: All chunks exist, command complete
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

        # Start command
        next(generator)
        generator.send(start_response)

        # Poll 1
        generator.send(poll_response_1)

        # Poll 2
        generator.send(poll_response_2)

        # Poll 3 - command completes
        try:
            generator.send(poll_response_3)
            pytest.fail("Generator should have stopped")
        except StopIteration as e:
            result = e.value

        # BUG: The actual output has duplicates!
        # Due to the bug, output is: CHUNK1 + CHUNK1 + CHUNK2 + CHUNK1 + CHUNK2 + CHUNK3
        buggy_output = "CHUNK1CHUNK1CHUNK2CHUNK1CHUNK2CHUNK3"

        # This assertion documents the bug - it PASSES because the bug exists
        # When the bug is fixed, this test will FAIL, and we should update it
        assert result.stdout == buggy_output, (
            f"Expected buggy duplicated output but got: {result.stdout!r}. "
            "If this test fails, the deduplication bug may have been fixed!"
        )

        # This is what the output SHOULD be (currently fails due to bug)
        # Uncomment this assertion after fixing the bug:
        # assert result.stdout == expected_output

    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_base64_decoding_fails_due_to_duplication(self, mock_time):
        """Test that base64 decoding fails when output is duplicated.

        This test reproduces the exact error seen in production:
        "Invalid base64-encoded string: number of data characters (X)
         cannot be 1 more than a multiple of 4"

        The trajectory capture code does:
            tar -czf - workspace/conversations | base64

        Then decodes the output with base64.b64decode(stdout).

        When the polling loop duplicates the base64 output, the decode fails.
        """
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        # Mock time to allow multiple poll iterations
        mock_time.time.side_effect = [0, 1, 2, 3, 4]
        mock_time.sleep = Mock()

        # Create some base64 data (simulating tar output)
        # Use data that when split and duplicated will produce invalid base64
        original_data = b"Test data!" * 5
        base64_encoded = base64.b64encode(original_data).decode("ascii")

        # Split into 3 chunks with sizes that DON'T align with base64's 4-char groups
        # When duplicated, the total length becomes non-multiple-of-4, causing
        # "Incorrect padding" error
        # Original: 68 chars, chunk1=17, chunk2=17, chunk3=34
        # Buggy output: 17 + 17 + 17 + 17 + 17 + 34 = 119 chars (119 % 4 = 3 -> error!)
        chunk1 = base64_encoded[:17]  # 17 chars
        chunk2 = base64_encoded[17:34]  # 17 chars
        chunk3 = base64_encoded[34:]  # 34 chars

        # Verify original decodes correctly
        assert base64.b64decode(base64_encoded) == original_data

        # Mock start response
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

        # Poll 2: Chunks 1 and 2 (API returns all events from the beginning)
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

        # Poll 3: All chunks (API returns all events)
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
                    "exit_code": 0,  # Command completes here
                },
            ]
        }

        generator = mixin._execute_command_generator(
            "tar -czf - workspace/conversations | base64", None, 30.0
        )

        # Start command
        next(generator)
        generator.send(start_response)

        # Poll 1
        generator.send(poll_response_1)

        # Poll 2
        generator.send(poll_response_2)

        # Poll 3 - command completes
        try:
            generator.send(poll_response_3)
            pytest.fail("Generator should have stopped")
        except StopIteration as e:
            result = e.value

        # Due to the bug, the output is duplicated
        # Output is: chunk1 + chunk1 + chunk2 + chunk1 + chunk2 + chunk3
        duplicated_output = result.stdout

        # Calculate what the buggy output should be
        buggy_output = chunk1 + chunk1 + chunk2 + chunk1 + chunk2 + chunk3

        # Verify the bug produces the expected duplicated output
        assert duplicated_output == buggy_output, (
            "Expected duplicated output pattern but got different result"
        )

        # Verify the output is NOT the correct base64
        assert duplicated_output != base64_encoded, (
            "Output should be corrupted due to duplication bug"
        )

        # The duplicated output is longer than the original
        assert len(duplicated_output) > len(base64_encoded), (
            f"Duplicated output ({len(duplicated_output)} chars) should be longer "
            f"than original ({len(base64_encoded)} chars)"
        )

        # Now try to decode the duplicated base64 - this should fail!
        # This reproduces the exact error from production logs:
        # "Incorrect padding" or "Invalid base64-encoded string"
        with pytest.raises(Exception) as exc_info:
            base64.b64decode(duplicated_output)

        # The error should be about invalid base64
        error_message = str(exc_info.value)
        assert (
            "Incorrect padding" in error_message
            or "Invalid base64" in error_message
            or "cannot be" in error_message  # "cannot be 1 more than a multiple of 4"
        ), f"Expected base64 decode error but got: {error_message}"

    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_single_poll_no_duplication(self, mock_time):
        """Test that single poll iteration works correctly (no duplication).

        This test verifies that when a command completes within a single
        poll iteration, the output is correct. This explains why the bug
        only affects slow/large commands.
        """
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        # Mock time - command completes immediately
        mock_time.time.side_effect = [0, 1]
        mock_time.sleep = Mock()

        # Mock start response
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

        # Start command
        next(generator)
        generator.send(start_response)

        # Single poll - command completes
        try:
            generator.send(poll_response)
            pytest.fail("Generator should have stopped")
        except StopIteration as e:
            result = e.value

        # With single poll, output is correct (no duplication)
        assert result.stdout == "CHUNK1CHUNK2CHUNK3"


class TestProposedFix:
    """Tests for the proposed fix using event deduplication.

    These tests will pass once the fix is implemented.
    The fix should track seen event IDs and skip duplicates.
    """

    @pytest.mark.skip(reason="Enable after implementing the fix")
    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_deduplication_prevents_duplicates(self, mock_time):
        """Test that the fix prevents output duplication."""
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        mock_time.time.side_effect = [0, 1, 2, 3, 4]
        mock_time.sleep = Mock()

        start_response = Mock()
        start_response.raise_for_status = Mock()
        start_response.json.return_value = {"id": "cmd-123"}

        # Simulate accumulating events across polls
        poll_response_1 = Mock()
        poll_response_1.raise_for_status = Mock()
        poll_response_1.json.return_value = {
            "items": [
                {"id": "e1", "kind": "BashOutput", "stdout": "A", "exit_code": None},
            ]
        }

        poll_response_2 = Mock()
        poll_response_2.raise_for_status = Mock()
        poll_response_2.json.return_value = {
            "items": [
                {"id": "e1", "kind": "BashOutput", "stdout": "A", "exit_code": None},
                {"id": "e2", "kind": "BashOutput", "stdout": "B", "exit_code": None},
            ]
        }

        poll_response_3 = Mock()
        poll_response_3.raise_for_status = Mock()
        poll_response_3.json.return_value = {
            "items": [
                {"id": "e1", "kind": "BashOutput", "stdout": "A", "exit_code": None},
                {"id": "e2", "kind": "BashOutput", "stdout": "B", "exit_code": None},
                {"id": "e3", "kind": "BashOutput", "stdout": "C", "exit_code": 0},
            ]
        }

        generator = mixin._execute_command_generator("test", None, 30.0)

        next(generator)
        generator.send(start_response)
        generator.send(poll_response_1)
        generator.send(poll_response_2)

        try:
            generator.send(poll_response_3)
            pytest.fail("Generator should have stopped")
        except StopIteration as e:
            result = e.value

        # After fix: output should be exactly "ABC" with no duplicates
        assert result.stdout == "ABC", (
            f"Expected 'ABC' but got '{result.stdout}'. "
            "Deduplication should prevent duplicates."
        )

    @pytest.mark.skip(reason="Enable after implementing the fix")
    @patch("openhands.sdk.workspace.remote.remote_workspace_mixin.time")
    def test_base64_decodes_correctly_after_fix(self, mock_time):
        """Test that base64 decoding works after the fix."""
        mixin = RemoteWorkspaceMixinHelper(
            host="http://localhost:8000", working_dir="workspace"
        )

        mock_time.time.side_effect = [0, 1, 2, 3]
        mock_time.sleep = Mock()

        # Create base64 data
        original_data = b"Test data for trajectory capture"
        base64_encoded = base64.b64encode(original_data).decode("ascii")

        chunk1 = base64_encoded[: len(base64_encoded) // 2]
        chunk2 = base64_encoded[len(base64_encoded) // 2 :]

        start_response = Mock()
        start_response.raise_for_status = Mock()
        start_response.json.return_value = {"id": "cmd-456"}

        poll_response_1 = Mock()
        poll_response_1.raise_for_status = Mock()
        poll_response_1.json.return_value = {
            "items": [
                {"id": "e1", "kind": "BashOutput", "stdout": chunk1, "exit_code": None},
            ]
        }

        poll_response_2 = Mock()
        poll_response_2.raise_for_status = Mock()
        poll_response_2.json.return_value = {
            "items": [
                {"id": "e1", "kind": "BashOutput", "stdout": chunk1, "exit_code": None},
                {"id": "e2", "kind": "BashOutput", "stdout": chunk2, "exit_code": 0},
            ]
        }

        generator = mixin._execute_command_generator("base64_command", None, 30.0)

        next(generator)
        generator.send(start_response)
        generator.send(poll_response_1)

        try:
            generator.send(poll_response_2)
            pytest.fail("Generator should have stopped")
        except StopIteration as e:
            result = e.value

        # After fix: output should be valid base64
        assert result.stdout == base64_encoded

        # Decoding should work
        decoded = base64.b64decode(result.stdout)
        assert decoded == original_data
