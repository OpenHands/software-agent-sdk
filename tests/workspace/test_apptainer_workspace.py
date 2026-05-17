"""Test ApptainerWorkspace import and GPU passthrough behavior."""

from unittest.mock import Mock, patch

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

        def _create_workspace(*, enable_gpu: bool = False):
            with (
                patch.object(ApptainerWorkspace, "_start_container"),
                patch.object(ApptainerWorkspace, "_wait_for_health"),
            ):
                workspace = ApptainerWorkspace(
                    sif_file=str(sif_path),
                    host_port=8000,
                    detach_logs=False,
                    enable_gpu=enable_gpu,
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
