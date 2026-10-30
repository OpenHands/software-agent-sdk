"""Test DockerWorkspace import and basic functionality."""

import gc
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from pydantic import ValidationError

from openhands.workspace import (
    ApptainerWorkspace,
    DockerDevWorkspace,
    DockerWorkspace,
)


@pytest.fixture
def mock_docker_workspace():
    """Fixture to create a mocked DockerWorkspace with minimal setup."""

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

    assert DockerWorkspace is not None
    assert hasattr(DockerWorkspace, "__init__")


def test_docker_workspace_inheritance():
    """Test that DockerWorkspace inherits from RemoteWorkspace."""
    from openhands.sdk.workspace import RemoteWorkspace

    assert issubclass(DockerWorkspace, RemoteWorkspace)


def test_docker_dev_workspace_import():
    """Test that DockerDevWorkspace can be imported from the new package."""

    assert DockerDevWorkspace is not None
    assert hasattr(DockerDevWorkspace, "__init__")


def test_docker_dev_workspace_inheritance():
    """Test that DockerDevWorkspace inherits from DockerWorkspace."""

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

    assert "server_image" in DockerWorkspace.model_fields
    assert "base_image" not in DockerWorkspace.model_fields


def test_docker_dev_workspace_has_build_fields():
    """Test that DockerDevWorkspace has both base_image and server_image fields."""

    # DockerDevWorkspace should have both fields for flexibility
    assert "server_image" in DockerDevWorkspace.model_fields
    assert "base_image" in DockerDevWorkspace.model_fields
    assert "target" in DockerDevWorkspace.model_fields


def test_removed_mount_dir_input_fails_loudly():
    with pytest.raises(ValidationError, match="mount_dir has been removed"):
        DockerWorkspace.model_validate(
            {"server_image": "test:latest", "mount_dir": "/tmp/old"}
        )


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


def test_docker_workspace_publishes_ports_on_loopback(mock_docker_workspace):
    with (
        patch(
            "openhands.workspace.docker.workspace.check_port_available",
            return_value=True,
        ),
        patch.object(DockerWorkspace, "_wait_for_health"),
    ):
        with patch.object(DockerWorkspace, "_start_container"):
            workspace = DockerWorkspace(
                server_image="test:latest",
                host_port=8010,
                extra_ports=True,
                detach_logs=False,
            )
        workspace, mock_exec = workspace, mock_docker_workspace()[1]
        mock_exec.reset_mock()
        mock_exec.return_value = Mock(returncode=0, stdout="container_123", stderr="")

        workspace._start_container("test:latest", None)

        run_cmd = next(
            call[0][0] for call in mock_exec.call_args_list if "run" in call[0][0]
        )
        assert "127.0.0.1:8010:8000" in run_cmd
        assert "127.0.0.1:8011:8001" in run_cmd
        assert run_cmd[run_cmd.index("--host") + 1] == "0.0.0.0"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, None),
        ({"SESSION_API_KEY": "legacy-key"}, "legacy-key"),
        ({"OH_SESSION_API_KEYS_0": "current-key"}, "current-key"),
        (
            {
                "SESSION_API_KEY": "legacy-key",
                "OH_SESSION_API_KEYS_0": "current-key",
            },
            "current-key",
        ),
    ],
)
def test_docker_workspace_uses_forwarded_session_key(
    mock_docker_workspace, monkeypatch, env, expected
):
    """The client key must match what the server in this container accepts.

    `forward_env` forwards both variables, and the server resolves the V1
    `OH_SESSION_API_KEYS_0` first (V0 `SESSION_API_KEY` is only a fallback), so
    with both set the workspace has to send the V1 key or the container
    rejects it.
    """
    monkeypatch.delenv("SESSION_API_KEY", raising=False)
    monkeypatch.delenv("OH_SESSION_API_KEYS_0", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with (
        patch(
            "openhands.workspace.docker.workspace.check_port_available",
            return_value=True,
        ),
        patch.object(DockerWorkspace, "_wait_for_health"),
    ):
        workspace, mock_exec = mock_docker_workspace()
        workspace.host_port = 8010
        workspace.detach_logs = False
        mock_exec.reset_mock()
        mock_exec.return_value = Mock(returncode=0, stdout="container_123", stderr="")

        workspace._start_container("test:latest", None)

    assert workspace.api_key == expected


# ===========================================================================
# health_check_timeout tests for DockerWorkspace and ApptainerWorkspace
# ===========================================================================


@pytest.mark.parametrize("cls", [DockerWorkspace, ApptainerWorkspace])
def test_health_check_timeout_default(cls):
    """Test that health_check_timeout defaults to 120.0 seconds."""
    assert cls.model_fields["health_check_timeout"].default == 120.0


@pytest.mark.parametrize("cls", [DockerWorkspace, ApptainerWorkspace])
def test_health_check_timeout_rejects_non_positive(cls):
    """Test that health_check_timeout rejects zero and negative values."""
    with pytest.raises(ValidationError, match="greater than 0"):
        # Attempt to create with invalid timeout - we need to mock startup
        with patch.object(cls, "model_post_init"):
            cls.model_validate(
                {"server_image": "test:latest", "health_check_timeout": 0}
            )

    with pytest.raises(ValidationError, match="greater than 0"):
        with patch.object(cls, "model_post_init"):
            cls.model_validate(
                {"server_image": "test:latest", "health_check_timeout": -10.0}
            )


def test_docker_workspace_startup_uses_health_check_timeout():
    """Test that _start_container passes health_check_timeout to _wait_for_health."""
    with (
        patch(
            "openhands.workspace.docker.workspace.check_port_available",
            return_value=True,
        ),
        patch(
            "openhands.workspace.docker.workspace.find_available_tcp_port",
            return_value=8000,
        ),
        patch("openhands.workspace.docker.workspace.execute_command") as mock_exec,
        patch.object(DockerWorkspace, "_wait_for_health") as mock_wait,
        patch("openhands.workspace.docker.workspace.RemoteWorkspace.model_post_init"),
    ):
        mock_exec.return_value = Mock(returncode=0, stdout="container_123", stderr="")
        DockerWorkspace(server_image="test:latest", health_check_timeout=60.0)
        mock_wait.assert_called_once_with(timeout=60.0)


def test_docker_workspace_resume_uses_health_check_timeout():
    """Test that resume() passes health_check_timeout to _wait_for_health."""
    with patch.object(DockerWorkspace, "_start_container"):
        with patch("openhands.workspace.docker.workspace.execute_command") as mock_exec:
            mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")
            workspace = DockerWorkspace(
                server_image="test:latest", health_check_timeout=30.0
            )

    workspace._container_id = "container_id_123"

    with (
        patch("openhands.workspace.docker.workspace.execute_command") as mock_exec,
        patch.object(workspace, "_wait_for_health") as mock_wait,
    ):
        mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")
        workspace.resume()
        mock_wait.assert_called_once_with(timeout=30.0)


def test_apptainer_workspace_startup_uses_health_check_timeout():
    """Test that model_post_init passes health_check_timeout to _wait_for_health."""
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


# ===========================================================================
# DockerWorkspace startup failure paths
# ===========================================================================


def _docker_exec(**overrides):
    """execute_command fake keyed by docker subcommand; records every argv."""
    calls: list[list[str]] = []
    results = {
        "version": Mock(returncode=0, stdout="", stderr=""),
        "run": Mock(returncode=0, stdout="cid-123\n", stderr=""),
        "inspect": Mock(returncode=0, stdout="true\n", stderr=""),
        "logs": Mock(returncode=0, stdout="server log", stderr="boom"),
        **overrides,
    }

    def fake(cmd, *args, **kwargs):
        calls.append(cmd)
        return results.get(cmd[1], Mock(returncode=0, stdout="", stderr=""))

    return fake, calls


def _subcommands(calls: list[list[str]]) -> list[str]:
    return [cmd[1] for cmd in calls]


@pytest.fixture
def docker_startup(monkeypatch):
    monkeypatch.setattr(
        "openhands.workspace.docker.workspace.check_port_available", lambda p: True
    )
    monkeypatch.setattr("openhands.workspace.docker.workspace.time.sleep", Mock())


def test_docker_unavailable_fails_before_running_container(docker_startup, monkeypatch):
    fake, calls = _docker_exec(version=Mock(returncode=1, stdout="", stderr=""))
    monkeypatch.setattr("openhands.workspace.docker.workspace.execute_command", fake)

    with pytest.raises(RuntimeError, match="Docker is not available"):
        DockerWorkspace(server_image="test:latest", host_port=8123)

    assert _subcommands(calls) == ["version"]


def test_docker_run_failure_surfaces_stderr(docker_startup, monkeypatch):
    fake, calls = _docker_exec(
        run=Mock(returncode=125, stdout="", stderr="pull access denied")
    )
    monkeypatch.setattr("openhands.workspace.docker.workspace.execute_command", fake)

    with pytest.raises(RuntimeError, match="pull access denied"):
        DockerWorkspace(server_image="test:latest", host_port=8123)


def test_wait_for_health_reports_logs_when_container_exits(docker_startup, monkeypatch):
    fake, calls = _docker_exec(inspect=Mock(returncode=0, stdout="false\n"))
    monkeypatch.setattr("openhands.workspace.docker.workspace.execute_command", fake)
    monkeypatch.setattr(
        "openhands.workspace.docker.workspace.urlopen",
        Mock(side_effect=OSError("connection refused")),
    )
    with patch.object(DockerWorkspace, "_start_container"):
        workspace = DockerWorkspace(server_image="test:latest")
    workspace.host = "http://127.0.0.1:8123"
    workspace._container_id = "cid-123"

    with pytest.raises(RuntimeError, match="stopped unexpectedly") as excinfo:
        workspace._wait_for_health(timeout=60)

    assert "server log" in str(excinfo.value)
    assert "boom" in str(excinfo.value)
    workspace._container_id = None


def test_wait_for_health_times_out(docker_startup, monkeypatch):
    monkeypatch.setattr(
        "openhands.workspace.docker.workspace.urlopen",
        Mock(side_effect=OSError("connection refused")),
    )
    with patch.object(DockerWorkspace, "_start_container"):
        workspace = DockerWorkspace(server_image="test:latest")
    workspace.host = "http://127.0.0.1:8123"

    with pytest.raises(RuntimeError, match="failed to become healthy"):
        workspace._wait_for_health(timeout=0.01)


@pytest.mark.xfail(
    strict=True,
    reason="BUG: DockerWorkspace leaves the container running when startup fails "
    "after `docker run` (no cleanup in model_post_init)",
)
def test_failed_health_check_stops_container(docker_startup, monkeypatch):
    fake, calls = _docker_exec()
    monkeypatch.setattr("openhands.workspace.docker.workspace.execute_command", fake)

    with (
        patch.object(
            DockerWorkspace,
            "_wait_for_health",
            side_effect=RuntimeError("Container failed to become healthy in time"),
        ),
        pytest.raises(RuntimeError, match="healthy") as excinfo,
    ):
        DockerWorkspace(server_image="test:latest", host_port=8123, detach_logs=False)

    # excinfo keeps the half-built workspace alive, so __del__ has not run yet
    # and cannot stand in for a rollback inside the constructor.
    stopped_during_startup = ["docker", "stop", "cid-123"] in calls
    del excinfo
    gc.collect()  # let __del__ run while execute_command is still faked
    assert stopped_during_startup


@pytest.mark.xfail(
    strict=True,
    reason="BUG: an exhausted port range (find_available_tcp_port() == -1) "
    "raises OverflowError from socket.bind instead of a clear RuntimeError",
)
def test_no_free_port_raises_runtime_error(monkeypatch):
    fake, calls = _docker_exec()
    monkeypatch.setattr("openhands.workspace.docker.workspace.execute_command", fake)
    monkeypatch.setattr(
        "openhands.workspace.docker.workspace.find_available_tcp_port", lambda: -1
    )

    with pytest.raises(RuntimeError, match="not available"):
        DockerWorkspace(server_image="test:latest")

    assert calls == []


@pytest.mark.xfail(
    strict=True,
    reason="BUG: DockerWorkspace.__exit__ skips RemoteWorkspace.__exit__, so the "
    "AUTOMATION_CALLBACK_URL completion callback is never sent",
)
def test_exit_sends_completion_callback(mock_docker_workspace, monkeypatch):
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    workspace, _ = mock_docker_workspace()

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        workspace.__exit__(None, None, None)

        mock_client.post.assert_called_once()
        assert mock_client.post.call_args.args[0] == "https://svc.test/complete"


@pytest.mark.xfail(
    strict=True,
    reason="BUG: cleanup() run on the log-streaming thread (as __del__ does when "
    "that thread drops the last reference) raises 'cannot join current thread' "
    "before `docker stop`",
)
def test_cleanup_from_log_thread_still_stops_container(mock_docker_workspace):
    workspace, mock_exec = mock_docker_workspace()
    workspace._stop_logs = threading.Event()
    errors: list[Exception] = []

    def run_cleanup():
        try:
            workspace.cleanup()
        except Exception as e:
            errors.append(e)

    workspace._logs_thread = threading.Thread(target=run_cleanup)
    workspace._logs_thread.start()
    workspace._logs_thread.join(timeout=5)

    assert errors == []
    stop_calls = [c.args[0] for c in mock_exec.call_args_list if "stop" in c.args[0]]
    assert stop_calls == [["docker", "stop", "container_id_123"]]
