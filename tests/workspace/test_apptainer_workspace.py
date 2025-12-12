"""Test ApptainerWorkspace import and basic functionality."""


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
