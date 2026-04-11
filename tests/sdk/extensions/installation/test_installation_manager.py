from pathlib import Path

import pytest
from litellm import json
from pydantic import BaseModel

from openhands.sdk.extensions.installation import (
    InstallationInterface,
    InstallationManager,
    InstallationMetadata,
)


class MockExtension(BaseModel):
    name: str
    version: str
    description: str


class MockExtensionInstallationInterface(InstallationInterface):
    @staticmethod
    def load_from_dir(extension_dir: Path) -> MockExtension:
        extension_path: Path = extension_dir / "extension.json"
        with extension_path.open() as f:
            return MockExtension.model_validate_json(json.load(f))


@pytest.fixture
def mock_extension() -> MockExtension:
    """Builds an instance of the mock extension class."""
    return MockExtension(
        name="mock-extension", version="0.0.1", description="Mock extension"
    )


@pytest.fixture
def mock_extension_dir(mock_extension: MockExtension, tmp_path: Path) -> Path:
    """Builds a temporary directory for the mock extension, loadable using
    `load_from_dir` functions.
    """
    extension_dir: Path = tmp_path / "mock-extension"
    extension_dir.mkdir(parents=True, exist_ok=True)

    extension_path: Path = extension_dir / "extension.json"
    with extension_path.open("w") as f:
        json.dump(mock_extension.model_dump_json(), f)

    return extension_dir


@pytest.fixture
def installation_dir(tmp_path: Path) -> Path:
    """Builds an installation directory."""
    installation_dir: Path = tmp_path / "installed"
    installation_dir.mkdir(parents=True, exist_ok=True)
    return installation_dir


def test_install_from_local_path(
    mock_extension_dir: Path, installation_dir: Path, mock_extension: MockExtension
):
    """Test extensions can be installed from local source."""
    manager = InstallationManager(
        installation_dir=installation_dir,
        installation_interface=MockExtensionInstallationInterface(),
    )

    extension_info = manager.install(str(mock_extension_dir))

    # Verify the produced info matches the mock extension
    assert extension_info.name == mock_extension.name
    assert extension_info.version == mock_extension.version
    assert extension_info.description == mock_extension.description

    # Verify the extension was copied to the installation directory
    extension_dir = installation_dir / mock_extension.name
    assert extension_dir.exists()
    assert (extension_dir / "extension.json").exists()

    # Verify metadata was updated
    metadata = InstallationMetadata.load_from_dir(installation_dir)
    assert mock_extension.name in metadata.extensions


def test_install_already_exist_raises_error(
    mock_extension_dir: Path, installation_dir: Path
):
    """Tests that installing an existing plugin raises FileExistsError unless forced."""
    manager = InstallationManager(
        installation_dir=installation_dir,
        installation_interface=MockExtensionInstallationInterface(),
    )

    manager.install(mock_extension_dir)

    with pytest.raises(FileExistsError):
        manager.install(mock_extension_dir)

    assert manager.install(mock_extension_dir, force=True)


def test_install_with_force_overwrites(
    mock_extension_dir: Path, installation_dir: Path, mock_extension: MockExtension
):
    """Test that force=True overwrites existing installation."""
    manager = InstallationManager(
        installation_dir=installation_dir,
        installation_interface=MockExtensionInstallationInterface(),
    )

    manager.install(mock_extension_dir)

    marker_file = installation_dir / mock_extension.name / "marker.txt"
    marker_file.write_text("MARK")
    assert marker_file.exists()

    manager.install(mock_extension_dir, force=True)

    assert not marker_file.exists()
