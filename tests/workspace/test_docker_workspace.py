"""Test DockerWorkspace import and basic functionality."""


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
    """
    Test that DockerWorkspace can be imported without SDK project root.

    This is the key fix for issue #1196 - importing DockerWorkspace should not
    require the SDK project root since it doesn't do any image building.
    """
    # This import should not raise any errors about SDK project root
    from openhands.workspace import DockerWorkspace

    # Verify the class has the expected server_image field
    assert "server_image" in DockerWorkspace.model_fields
    # Verify the class does NOT have base_image field
    assert "base_image" not in DockerWorkspace.model_fields


def test_docker_dev_workspace_has_build_fields():
    """Test that DockerDevWorkspace has both base_image and server_image fields."""
    from openhands.workspace import DockerDevWorkspace

    # DockerDevWorkspace should have both fields for flexibility
    assert "server_image" in DockerDevWorkspace.model_fields
    assert "base_image" in DockerDevWorkspace.model_fields
    assert "target" in DockerDevWorkspace.model_fields
