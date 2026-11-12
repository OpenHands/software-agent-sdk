"""Test ApptainerWorkspace import and GPU passthrough behavior."""

import gc
import subprocess
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest


@pytest.fixture
def mock_apptainer_workspace(tmp_path):
    """Fixture to create a mocked ApptainerWorkspace with minimal setup."""
    from openhands.workspace import ApptainerWorkspace

    sif_path = tmp_path / "test.sif"
    sif_path.write_text("fake sif")

    with (
        patch("openhands.workspace.apptainer.workspace.execute_command") as mock_exec,
        patch(
            "openhands.workspace.apptainer.workspace.check_port_available",
            return_value=True,
        ),
    ):
        mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")

        def _create_workspace(
            *, enable_gpu: bool = False, extra_bind_mounts: list[str] | None = None
        ):
            with (
                patch.object(ApptainerWorkspace, "_start_container"),
                patch.object(ApptainerWorkspace, "_wait_for_health"),
            ):
                workspace = ApptainerWorkspace(
                    sif_file=str(sif_path),
                    host_port=8000,
                    detach_logs=False,
                    enable_gpu=enable_gpu,
                    extra_bind_mounts=extra_bind_mounts or [],
                )

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


def test_apptainer_workspace_has_gpu_field():
    """Test that ApptainerWorkspace exposes the GPU passthrough option."""
    from openhands.workspace import ApptainerWorkspace

    assert "enable_gpu" in ApptainerWorkspace.model_fields


@pytest.mark.parametrize("enable_gpu", [False, True])
def test_apptainer_workspace_gpu_passthrough_flag(
    mock_apptainer_workspace, enable_gpu: bool
):
    """Test that GPU passthrough toggles the Apptainer --nv flag."""
    workspace, _ = mock_apptainer_workspace(enable_gpu=enable_gpu)

    fake_process = Mock(stdout=None)
    with patch(
        "openhands.workspace.apptainer.workspace.subprocess.Popen",
        return_value=fake_process,
    ) as mock_popen:
        workspace._start_container()

    run_cmd = mock_popen.call_args.args[0]

    assert run_cmd[:2] == ["apptainer", "run"]
    assert ("--nv" in run_cmd) is enable_gpu
    assert workspace._sif_path in run_cmd

    workspace._process = None
    workspace._instance_name = None


def test_apptainer_workspace_extra_bind_mounts(mock_apptainer_workspace, monkeypatch):
    """Test that explicit and environment-provided bind mounts reach Apptainer."""
    monkeypatch.setenv("OPENHANDS_APPTAINER_EXTRA_BINDS", "/env/src:/env/dst:ro")
    workspace, _ = mock_apptainer_workspace(
        extra_bind_mounts=["/host/tokenizer:/host/tokenizer:ro"]
    )

    fake_process = Mock(stdout=None)
    with patch(
        "openhands.workspace.apptainer.workspace.subprocess.Popen",
        return_value=fake_process,
    ) as mock_popen:
        workspace._start_container()

    run_cmd = mock_popen.call_args.args[0]

    assert "--bind" in run_cmd
    assert "/host/tokenizer:/host/tokenizer:ro" in run_cmd
    assert "/env/src:/env/dst:ro" in run_cmd

    workspace._process = None
    workspace._instance_name = None


def test_undrained_logs_do_not_block_server(mock_apptainer_workspace):
    """With detach_logs=False nothing reads the server's output, so no pipe."""
    workspace, _ = mock_apptainer_workspace()  # detach_logs=False

    with patch(
        "openhands.workspace.apptainer.workspace.subprocess.Popen",
        return_value=Mock(stdout=None),
    ) as mock_popen:
        workspace._start_container()

    assert mock_popen.call_args.kwargs["stdout"] is subprocess.DEVNULL

    workspace._process = None
    workspace._instance_name = None


def test_failed_health_check_terminates_server_process(monkeypatch, tmp_path):
    from openhands.workspace import ApptainerWorkspace

    monkeypatch.setattr(
        "openhands.workspace.apptainer.workspace.execute_command",
        Mock(return_value=Mock(returncode=0, stdout="", stderr="")),
    )
    monkeypatch.setattr(
        "openhands.workspace.apptainer.workspace.check_port_available", lambda p: True
    )
    monkeypatch.setattr(
        "openhands.workspace.apptainer.workspace.Path.home", lambda: tmp_path
    )

    # Run a real long-lived child in place of `apptainer run`.
    real_popen = subprocess.Popen
    procs: list[subprocess.Popen] = []

    def fake_popen(cmd, **kwargs):
        proc = real_popen(
            [sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"],
            **kwargs,
        )
        procs.append(proc)
        return proc

    monkeypatch.setattr(
        "openhands.workspace.apptainer.workspace.subprocess.Popen", fake_popen
    )
    sif = tmp_path / "img.sif"
    sif.write_text("fake sif")

    try:
        with (
            patch.object(
                ApptainerWorkspace,
                "_wait_for_health",
                side_effect=RuntimeError("Container failed to become healthy in time"),
            ),
            pytest.raises(RuntimeError, match="healthy") as excinfo,
        ):
            ApptainerWorkspace(sif_file=str(sif), host_port=8000, detach_logs=False)

        # excinfo keeps the half-built workspace alive, so __del__ has not run yet.
        alive_after_failure = procs[0].poll() is None
        del excinfo
        gc.collect()
        assert not alive_after_failure
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()


def test_exit_sends_completion_callback(mock_apptainer_workspace, monkeypatch):
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    workspace, _ = mock_apptainer_workspace()

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        workspace.__exit__(None, None, None)

        mock_client.post.assert_called_once()
        assert mock_client.post.call_args.args[0] == "https://svc.test/complete"
