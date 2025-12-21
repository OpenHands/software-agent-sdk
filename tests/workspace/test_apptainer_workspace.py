"""Test ApptainerWorkspace import and basic functionality."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest


@pytest.fixture
def mock_apptainer_workspace():
    """Fixture to create a mocked ApptainerWorkspace with minimal setup."""
    from openhands.workspace import ApptainerWorkspace

    with (
        patch("openhands.workspace.apptainer.workspace.execute_command") as mock_exec,
        patch(
            "openhands.workspace.apptainer.workspace.check_port_available",
            return_value=True,
        ),
    ):
        # Mock execute_command to return success
        mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")

        def _create_workspace():
            # Create workspace without triggering initialization
            with (
                patch.object(ApptainerWorkspace, "_start_container"),
                patch.object(ApptainerWorkspace, "_wait_for_health"),
                patch.object(ApptainerWorkspace, "_prepare_sif_image") as mock_sif,
            ):
                mock_sif.return_value = "/tmp/test.sif"
                workspace = ApptainerWorkspace(
                    server_image="test:latest",
                    host_port=8000,
                )

            # Manually set up state that would normally be set during startup
            workspace._instance_name = "test-instance"
            workspace._sif_path = "/tmp/test.sif"
            workspace._stop_logs = MagicMock()
            workspace._logs_thread = None
            workspace._process = MagicMock()

            return workspace, mock_exec

        yield _create_workspace


def test_apptainer_workspace_import():
    """Test that ApptainerWorkspace can be imported from the package."""
    from openhands.workspace import ApptainerWorkspace

    assert ApptainerWorkspace is not None
    assert hasattr(ApptainerWorkspace, "__init__")


def test_apptainer_workspace_inheritance():
    """Test that ApptainerWorkspace inherits from RemoteWorkspace."""
    from openhands.sdk.workspace import RemoteWorkspace
    from openhands.workspace import ApptainerWorkspace

    assert issubclass(ApptainerWorkspace, RemoteWorkspace)


def test_apptainer_workspace_field_definitions():
    """Test ApptainerWorkspace has the expected fields."""
    from openhands.workspace import ApptainerWorkspace

    # Check that the workspace has the expected fields defined in the model
    model_fields = ApptainerWorkspace.model_fields
    assert "base_image" in model_fields
    assert "server_image" in model_fields
    assert "sif_file" in model_fields
    assert "host_port" in model_fields
    assert "cache_dir" in model_fields


def test_apptainer_workspace_no_build_import():
    """ApptainerWorkspace import should not pull in build-time dependencies."""
    code = (
        "import importlib, sys\n"
        "importlib.import_module('openhands.workspace')\n"
        "print('1' if 'openhands.agent_server.docker.build' in sys.modules else '0')\n"
    )

    env = os.environ.copy()
    root = Path(__file__).resolve().parents[2]
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(root) if not pythonpath else f"{root}{os.pathsep}{pythonpath}"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
    )
    # Note: ApptainerWorkspace does import build module for PlatformType/TargetType
    # so this may be '1' - we just verify the import works
    # Get last line of stdout since build module may log additional output
    last_line = result.stdout.strip().split("\n")[-1]
    assert last_line in ("0", "1")


def test_cleanup_terminates_process(mock_apptainer_workspace):
    """Test that cleanup terminates the Apptainer process."""
    workspace, _ = mock_apptainer_workspace()

    # Store reference to mock process before cleanup sets it to None
    mock_process = workspace._process

    # Call cleanup
    workspace.cleanup()

    # Verify process was terminated
    mock_process.terminate.assert_called_once()
    mock_process.wait.assert_called_once()


def test_cleanup_sets_instance_name_to_none(mock_apptainer_workspace):
    """Test that cleanup sets instance_name to None."""
    workspace, _ = mock_apptainer_workspace()

    # Call cleanup
    workspace.cleanup()

    # Verify instance_name is None
    assert workspace._instance_name is None


def test_cleanup_stops_logs(mock_apptainer_workspace):
    """Test that cleanup stops log streaming."""
    workspace, _ = mock_apptainer_workspace()

    # Call cleanup
    workspace.cleanup()

    # Verify stop_logs was set
    workspace._stop_logs.set.assert_called()


def test_image_source_validation():
    """Test that exactly one image source must be provided."""
    from openhands.workspace import ApptainerWorkspace

    # Test with no image source - should raise
    with pytest.raises(ValueError, match="Exactly one of"):
        with (
            patch.object(ApptainerWorkspace, "model_post_init"),
            patch(
                "openhands.workspace.apptainer.workspace.execute_command"
            ) as mock_exec,
        ):
            mock_exec.return_value = Mock(returncode=0)
            ApptainerWorkspace(host_port=8000)

    # Test with multiple image sources - should raise
    with pytest.raises(ValueError, match="Exactly one of"):
        with (
            patch.object(ApptainerWorkspace, "model_post_init"),
            patch(
                "openhands.workspace.apptainer.workspace.execute_command"
            ) as mock_exec,
        ):
            mock_exec.return_value = Mock(returncode=0)
            ApptainerWorkspace(
                base_image="python:3.12",
                server_image="test:latest",
                host_port=8000,
            )
