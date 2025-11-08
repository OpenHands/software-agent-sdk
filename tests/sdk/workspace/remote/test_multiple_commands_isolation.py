"""Test command output isolation for sequential execute_command calls.

This test verifies that executing multiple commands sequentially produces
isolated outputs, ensuring each command's result contains only its own output
without contamination from previous commands.
"""

from unittest.mock import Mock

from openhands.sdk.workspace.remote.remote_workspace_mixin import RemoteWorkspaceMixin


class _RemoteWorkspaceMixinForTest(RemoteWorkspaceMixin):
    """Concrete implementation of RemoteWorkspaceMixin for testing purposes."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def test_multiple_commands_use_different_command_ids():
    """Test that sequential commands use different command IDs in API params.

    Verifies that when multiple commands are executed sequentially,
    each one uses its own command_id for filtering events, preventing
    output contamination from previous commands.
    """
    mixin = _RemoteWorkspaceMixinForTest(
        host="http://localhost:8000", working_dir="/workspace"
    )

    # ==== First command ====
    start_response_1 = Mock()
    start_response_1.raise_for_status = Mock()
    start_response_1.json.return_value = {"id": "cmd-001"}

    generator_1 = mixin._execute_command_generator("ls -l /workspace", None, 30.0)

    # Start first command
    start_kwargs_1 = next(generator_1)
    assert start_kwargs_1["method"] == "POST"

    # Get poll request for first command
    poll_kwargs_1 = generator_1.send(start_response_1)

    # Verify first command filters by cmd-001
    params_1 = poll_kwargs_1["params"]
    assert "command_id__eq" in params_1
    assert params_1["command_id__eq"] == "cmd-001", (
        "First command should filter events by its command ID 'cmd-001'"
    )

    # ==== Second command ====
    start_response_2 = Mock()
    start_response_2.raise_for_status = Mock()
    start_response_2.json.return_value = {"id": "cmd-002"}

    generator_2 = mixin._execute_command_generator("ls -l ./", None, 30.0)

    # Start second command
    start_kwargs_2 = next(generator_2)
    assert start_kwargs_2["method"] == "POST"

    # Get poll request for second command
    poll_kwargs_2 = generator_2.send(start_response_2)

    # Verify second command filters by cmd-002 (NOT cmd-001)
    params_2 = poll_kwargs_2["params"]
    assert "command_id__eq" in params_2
    assert params_2["command_id__eq"] == "cmd-002", (
        "Second command should filter events by its OWN command ID 'cmd-002', "
        "not by the first command's ID. This ensures outputs are isolated."
    )

    # Verify the two commands use different command IDs
    assert params_1["command_id__eq"] != params_2["command_id__eq"], (
        "Sequential commands must use different command IDs to prevent "
        "output contamination"
    )


def test_command_id_filter_params_structure():
    """Test that command_id__eq and sort_order are separate params.

    Verifies that the API search params are correctly structured
    with separate keys for command_id filtering and sort_order,
    ensuring proper event filtering by command ID.
    """
    mixin = _RemoteWorkspaceMixinForTest(
        host="http://localhost:8000", working_dir="/workspace"
    )

    start_response = Mock()
    start_response.raise_for_status = Mock()
    start_response.json.return_value = {"id": "cmd-123"}

    generator = mixin._execute_command_generator("echo test", None, 30.0)

    # Start command
    start_kwargs = next(generator)
    assert start_kwargs["method"] == "POST"

    # Send start response, get poll request
    poll_kwargs = generator.send(start_response)

    # Verify the params dict has separate keys for filtering and sorting
    params = poll_kwargs["params"]

    print(f"\nActual params: {params}")
    print(f"Params keys: {list(params.keys())}")

    # Verify params structure is correct
    assert "command_id__eq" in params, (
        "Missing command_id__eq param for filtering events by command ID"
    )
    assert params["command_id__eq"] == "cmd-123", (
        "The command_id__eq param should filter by the command ID 'cmd-123'"
    )
    assert "sort_order" in params, (
        "Missing sort_order param for sorting events by timestamp"
    )
    assert params["sort_order"] == "TIMESTAMP", (
        "The sort_order param should be set to 'TIMESTAMP'"
    )
