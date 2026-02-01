"""Test pause and resume functionality for workspace classes."""

from unittest.mock import MagicMock, Mock, patch

import pytest


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_docker_workspace():
    """Create a mocked DockerWorkspace with minimal setup."""
    from openhands.workspace import DockerWorkspace

    with patch("openhands.workspace.docker.workspace.execute_command") as mock_exec:
        mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")

        with patch.object(DockerWorkspace, "_start_container"):
            workspace = DockerWorkspace(server_image="test:latest")

        workspace._container_id = "container_id_123"
        workspace._image_name = "test:latest"
        workspace._stop_logs = MagicMock()
        workspace._logs_thread = None

        yield workspace, mock_exec


@pytest.fixture
def mock_api_workspace():
    """Create a mocked APIRemoteWorkspace with minimal setup."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime"):
        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

    workspace._runtime_id = "runtime-123"
    workspace._runtime_url = "https://runtime.example.com"
    workspace._session_api_key = "session-key"
    workspace.host = workspace._runtime_url

    yield workspace


@pytest.fixture
def mock_cloud_workspace():
    """Create a mocked OpenHandsCloudWorkspace with minimal setup."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://app.all-hands.dev",
            cloud_api_key="test-key",
        )

    workspace._sandbox_id = "sandbox-123"
    workspace._session_api_key = "session-key"
    workspace.host = "https://agent-server.example.com"

    yield workspace


# =============================================================================
# LocalWorkspace Tests
# =============================================================================


def test_local_workspace_pause_is_noop():
    """Test that pause() is a no-op for LocalWorkspace."""
    from openhands.sdk.workspace import LocalWorkspace

    workspace = LocalWorkspace(working_dir="/tmp")
    # Should not raise
    workspace.pause()


def test_local_workspace_resume_is_noop():
    """Test that resume() is a no-op for LocalWorkspace."""
    from openhands.sdk.workspace import LocalWorkspace

    workspace = LocalWorkspace(working_dir="/tmp")
    # Should not raise
    workspace.resume()


# =============================================================================
# DockerWorkspace Tests
# =============================================================================


def test_docker_workspace_pause_calls_docker_pause(mock_docker_workspace):
    """Test that pause() calls docker pause command."""
    workspace, mock_exec = mock_docker_workspace

    workspace.pause()

    # Verify docker pause was called
    calls = [c[0][0] for c in mock_exec.call_args_list]
    pause_calls = [c for c in calls if "pause" in c and "docker" in c]
    assert len(pause_calls) == 1
    assert "container_id_123" in pause_calls[0]


def test_docker_workspace_resume_calls_docker_unpause(mock_docker_workspace):
    """Test that resume() calls docker unpause command."""
    workspace, mock_exec = mock_docker_workspace
    workspace.host_port = 8000

    # Mock _wait_for_health
    with patch.object(workspace, "_wait_for_health"):
        workspace.resume()

    # Verify docker unpause was called
    calls = [c[0][0] for c in mock_exec.call_args_list]
    unpause_calls = [c for c in calls if "unpause" in c and "docker" in c]
    assert len(unpause_calls) == 1
    assert "container_id_123" in unpause_calls[0]


def test_docker_workspace_pause_raises_if_no_container():
    """Test that pause() raises RuntimeError if container not running."""
    from openhands.workspace import DockerWorkspace

    with patch.object(DockerWorkspace, "_start_container"):
        with patch("openhands.workspace.docker.workspace.execute_command") as mock_exec:
            mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")
            workspace = DockerWorkspace(server_image="test:latest")

    workspace._container_id = None

    with pytest.raises(RuntimeError, match="container is not running"):
        workspace.pause()


def test_docker_workspace_resume_raises_if_no_container():
    """Test that resume() raises RuntimeError if container not running."""
    from openhands.workspace import DockerWorkspace

    with patch.object(DockerWorkspace, "_start_container"):
        with patch("openhands.workspace.docker.workspace.execute_command") as mock_exec:
            mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")
            workspace = DockerWorkspace(server_image="test:latest")

    workspace._container_id = None

    with pytest.raises(RuntimeError, match="container is not running"):
        workspace.resume()


# =============================================================================
# APIRemoteWorkspace Tests
# =============================================================================


def test_api_workspace_pause_calls_api_endpoint(mock_api_workspace):
    """Test that pause() calls /pause API endpoint."""
    workspace = mock_api_workspace

    with patch.object(workspace, "_send_api_request") as mock_request:
        workspace.pause()

        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"
        assert "/pause" in call_args[0][1]


def test_api_workspace_resume_calls_api_endpoint(mock_api_workspace):
    """Test that resume() calls /resume API endpoint."""
    workspace = mock_api_workspace

    with patch.object(workspace, "_resume_runtime") as mock_resume:
        with patch.object(workspace, "_wait_until_runtime_alive"):
            workspace.resume()
            mock_resume.assert_called_once()


def test_api_workspace_pause_raises_if_no_runtime():
    """Test that pause() raises RuntimeError if runtime not running."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime"):
        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

    workspace._runtime_id = None

    with pytest.raises(RuntimeError, match="runtime is not running"):
        workspace.pause()


def test_api_workspace_resume_raises_if_no_runtime():
    """Test that resume() raises RuntimeError if runtime not running."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime"):
        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

    workspace._runtime_id = None

    with pytest.raises(RuntimeError, match="runtime is not running"):
        workspace.resume()


# =============================================================================
# OpenHandsCloudWorkspace Tests
# =============================================================================


def test_cloud_workspace_pause_raises_not_implemented(mock_cloud_workspace):
    """Test that pause() raises NotImplementedError."""
    workspace = mock_cloud_workspace

    with pytest.raises(NotImplementedError, match="not yet supported"):
        workspace.pause()


def test_cloud_workspace_resume_calls_resume_sandbox(mock_cloud_workspace):
    """Test that resume() calls _resume_sandbox()."""
    workspace = mock_cloud_workspace

    with patch.object(workspace, "_resume_sandbox") as mock_resume:
        with patch.object(workspace, "_wait_until_sandbox_ready"):
            workspace.resume()
            mock_resume.assert_called_once()


def test_cloud_workspace_resume_raises_if_no_sandbox():
    """Test that resume() raises RuntimeError if sandbox not running."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://app.all-hands.dev",
            cloud_api_key="test-key",
        )

    workspace._sandbox_id = None

    with pytest.raises(RuntimeError, match="sandbox is not running"):
        workspace.resume()
