"""Test DockerWorkspace import and basic functionality."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest


@pytest.fixture
def mock_docker_workspace():
    """Fixture to create a mocked DockerWorkspace with minimal setup."""
    from openhands.workspace import DockerWorkspace

    with patch("openhands.workspace.docker.workspace.execute_command") as mock_exec:
        # Mock execute_command to return success
        mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")

        def _create_workspace(cleanup_image=False):
            # Create workspace without triggering initialization
            with patch.object(DockerWorkspace, "_start_container"):
                workspace = DockerWorkspace(
                    server_image="test:latest", cleanup_image=cleanup_image
                )

            # Manually set up state that would normally be set during startup
            workspace._container_id = "container_id_123"
            workspace._image_name = "test:latest"
            workspace._stop_logs = MagicMock()
            workspace._logs_thread = None

            return workspace, mock_exec

        yield _create_workspace


def test_docker_workspace_import():
    """Test that DockerWorkspace can be imported from the new package."""
    from openhands.workspace import DockerWorkspace

    assert DockerWorkspace is not None
    assert hasattr(DockerWorkspace, "__init__")


def test_docker_workspace_inheritance():
    """Test that DockerWorkspace inherits from RemoteWorkspace."""
    from openhands.sdk.workspace import RemoteWorkspace
    from openhands.workspace import DockerWorkspace

    assert issubclass(DockerWorkspace, RemoteWorkspace)


def test_docker_dev_workspace_import():
    """Test that DockerDevWorkspace can be imported from the new package."""
    from openhands.workspace import DockerDevWorkspace

    assert DockerDevWorkspace is not None
    assert hasattr(DockerDevWorkspace, "__init__")


def test_docker_dev_workspace_inheritance():
    """Test that DockerDevWorkspace inherits from DockerWorkspace."""
    from openhands.workspace import DockerDevWorkspace, DockerWorkspace

    assert issubclass(DockerDevWorkspace, DockerWorkspace)


def test_docker_workspace_no_build_import():
    """DockerWorkspace import should not pull in build-time dependencies."""
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
    assert result.stdout.strip() == "0"

    from openhands.workspace import DockerWorkspace

    assert "server_image" in DockerWorkspace.model_fields
    assert "base_image" not in DockerWorkspace.model_fields


def test_docker_dev_workspace_has_build_fields():
    """Test that DockerDevWorkspace has both base_image and server_image fields."""
    from openhands.workspace import DockerDevWorkspace

    # DockerDevWorkspace should have both fields for flexibility
    assert "server_image" in DockerDevWorkspace.model_fields
    assert "base_image" in DockerDevWorkspace.model_fields
    assert "target" in DockerDevWorkspace.model_fields


def test_cleanup_without_image_deletion(mock_docker_workspace):
    """Test that cleanup with cleanup_image=False does not delete the image."""
    workspace, mock_exec = mock_docker_workspace(cleanup_image=False)

    # Call cleanup
    workspace.cleanup()

    # Verify docker rmi was NOT called
    calls = mock_exec.call_args_list
    rmi_calls = [c for c in calls if c[0] and "rmi" in str(c[0])]
    assert len(rmi_calls) == 0


def test_cleanup_with_image_deletion(mock_docker_workspace):
    """Test that cleanup with cleanup_image=True deletes the Docker image."""
    workspace, mock_exec = mock_docker_workspace(cleanup_image=True)

    # Call cleanup
    workspace.cleanup()

    # Verify docker rmi was called with correct arguments
    calls = mock_exec.call_args_list
    rmi_calls = [c for c in calls if c[0] and "rmi" in str(c[0])]
    assert len(rmi_calls) == 1

    # Verify the command includes -f flag and correct image name
    rmi_call_args = rmi_calls[0][0][0]
    assert "docker" in rmi_call_args
    assert "rmi" in rmi_call_args
    assert "-f" in rmi_call_args
    assert "test:latest" in rmi_call_args
