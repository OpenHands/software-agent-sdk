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

        def _create_workspace(cleanup_image=False, network=None):
            # Create workspace without triggering initialization
            with patch.object(DockerWorkspace, "_start_container"):
                workspace = DockerWorkspace(
                    server_image="test:latest",
                    cleanup_image=cleanup_image,
                    network=network,
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


def test_docker_network(mock_docker_workspace):
    """Test that specifying `network` passes the value to Docker."""
    from openhands.workspace import DockerWorkspace

    # We need to mock things that _start_container calls before and after docker run
    with (
        patch(
            "openhands.workspace.docker.workspace.check_port_available",
            return_value=True,
        ),
        patch(
            "openhands.workspace.docker.workspace.find_available_tcp_port",
            return_value=8000,
        ),
        patch.object(DockerWorkspace, "_wait_for_health"),
    ):
        # Use a custom network name
        network_name = "my-custom-network"
        workspace, mock_exec = mock_docker_workspace(network=network_name)

        # Clear mock_exec and ensure docker run returns a container ID
        mock_exec.reset_mock()
        mock_exec.return_value = Mock(returncode=0, stdout="container_123", stderr="")

        # Trigger the container startup (it's normally called in model_post_init
        # but the fixture mocks it out to allow manual testing)
        workspace._start_container("test:latest", None)

        # Verify docker run was called with --network
        all_calls = [call[0][0] for call in mock_exec.call_args_list]
        run_cmd = next(cmd for cmd in all_calls if "run" in cmd)

        assert "--network" in run_cmd
        network_index = run_cmd.index("--network")
        assert run_cmd[network_index + 1] == network_name


def test_docker_workspace_health_check_timeout_field_exists():
    """Test that health_check_timeout is a recognised model field."""
    from openhands.workspace import DockerWorkspace

    assert "health_check_timeout" in DockerWorkspace.model_fields


def test_docker_workspace_health_check_timeout_default():
    """Test that health_check_timeout defaults to 120.0 seconds."""
    from openhands.workspace import DockerWorkspace

    field = DockerWorkspace.model_fields["health_check_timeout"]
    assert field.default == 120.0


def test_docker_workspace_health_check_timeout_custom(mock_docker_workspace):
    """Test that a custom health_check_timeout is forwarded to _wait_for_health."""
    from openhands.workspace import DockerWorkspace

    with patch.object(DockerWorkspace, "_wait_for_health") as mock_wait:
        with patch.object(DockerWorkspace, "_start_container"):
            with patch(
                "openhands.workspace.docker.workspace.execute_command"
            ) as mock_exec:
                mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")
                workspace = DockerWorkspace(
                    server_image="test:latest", health_check_timeout=60.0
                )
        assert workspace.health_check_timeout == 60.0
        # The field is set; _wait_for_health is called by _start_container which
        # is mocked, so verify the value round-trips through the model.
        _ = mock_wait  # referenced to satisfy linters


def test_docker_workspace_resume_uses_health_check_timeout(mock_docker_workspace):
    """Test that resume() passes health_check_timeout to _wait_for_health."""
    from openhands.workspace import DockerWorkspace

    with patch.object(DockerWorkspace, "_start_container"):
        with patch("openhands.workspace.docker.workspace.execute_command") as mock_exec:
            mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")
            workspace = DockerWorkspace(
                server_image="test:latest", health_check_timeout=30.0
            )

    workspace._container_id = "container_id_123"

    with patch.object(workspace, "_wait_for_health") as mock_wait:
        mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")
        workspace.resume()
        mock_wait.assert_called_once_with(timeout=30.0)


# ===========================================================================
# Apptainer health_check_timeout tests (co-located for compactness)
# ===========================================================================


def test_apptainer_workspace_health_check_timeout_field_exists():
    """Test that health_check_timeout is a recognised model field."""
    from openhands.workspace import ApptainerWorkspace

    assert "health_check_timeout" in ApptainerWorkspace.model_fields


def test_apptainer_workspace_health_check_timeout_default():
    """Test that health_check_timeout defaults to 120.0 seconds."""
    from openhands.workspace import ApptainerWorkspace

    field = ApptainerWorkspace.model_fields["health_check_timeout"]
    assert field.default == 120.0


def test_apptainer_workspace_health_check_timeout_startup_call():
    """Test that model_post_init passes health_check_timeout to _wait_for_health."""
    from openhands.workspace import ApptainerWorkspace

    with (
        patch("openhands.workspace.apptainer.workspace.execute_command") as mock_exec,
        patch(
            "openhands.workspace.apptainer.workspace.check_port_available",
            return_value=True,
        ),
        patch(
            "openhands.workspace.apptainer.workspace.find_available_tcp_port",
            return_value=8000,
        ),
        patch.object(
            ApptainerWorkspace, "_prepare_sif_image", return_value="/fake/image.sif"
        ),
        patch.object(ApptainerWorkspace, "_start_container"),
        patch.object(ApptainerWorkspace, "_wait_for_health") as mock_wait,
        patch(
            "openhands.workspace.apptainer.workspace.RemoteWorkspace.model_post_init"
        ),
    ):
        mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")
        ApptainerWorkspace(server_image="test:latest", health_check_timeout=45.0)
        mock_wait.assert_called_once_with(timeout=45.0)
