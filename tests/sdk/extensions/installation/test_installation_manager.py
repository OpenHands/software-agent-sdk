import json
import shutil
from pathlib import Path

import pytest
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


def _write_mock_extension(
    directory: Path,
    name: str = "mock-extension",
    version: str = "0.0.1",
    description: str = "Mock extension",
) -> Path:
    """Write a mock extension manifest to a directory."""
    directory.mkdir(parents=True, exist_ok=True)
    ext = MockExtension(name=name, version=version, description=description)
    with (directory / "extension.json").open("w") as f:
        json.dump(ext.model_dump_json(), f)
    return directory


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
    return _write_mock_extension(
        tmp_path / "mock-extension",
        name=mock_extension.name,
        version=mock_extension.version,
        description=mock_extension.description,
    )


@pytest.fixture
def installation_dir(tmp_path: Path) -> Path:
    """Builds an installation directory."""
    installation_dir: Path = tmp_path / "installed"
    installation_dir.mkdir(parents=True, exist_ok=True)
    return installation_dir


@pytest.fixture
def manager(installation_dir: Path) -> InstallationManager[MockExtension]:
    """Builds an InstallationManager with the mock interface."""
    return InstallationManager(
        installation_dir=installation_dir,
        installation_interface=MockExtensionInstallationInterface(),
    )


# ============================================================================
# Install Tests
# ============================================================================


def test_install_from_local_path(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
    installation_dir: Path,
    mock_extension: MockExtension,
):
    """Test extensions can be installed from local source."""
    extension_info = manager.install(str(mock_extension_dir))

    assert extension_info.name == mock_extension.name
    assert extension_info.version == mock_extension.version
    assert extension_info.description == mock_extension.description

    extension_dir = installation_dir / mock_extension.name
    assert extension_dir.exists()
    assert (extension_dir / "extension.json").exists()

    metadata = InstallationMetadata.load_from_dir(installation_dir)
    assert mock_extension.name in metadata.extensions


def test_install_already_exist_raises_error(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
):
    """Test that installing an existing extension raises FileExistsError."""
    manager.install(mock_extension_dir)

    with pytest.raises(FileExistsError):
        manager.install(mock_extension_dir)

    assert manager.install(mock_extension_dir, force=True)


def test_install_with_force_overwrites(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
    installation_dir: Path,
    mock_extension: MockExtension,
):
    """Test that force=True overwrites existing installation."""
    manager.install(mock_extension_dir)

    marker_file = installation_dir / mock_extension.name / "marker.txt"
    marker_file.write_text("MARK")
    assert marker_file.exists()

    manager.install(mock_extension_dir, force=True)

    assert not marker_file.exists()


def test_install_invalid_extension_name_raises_error(
    manager: InstallationManager[MockExtension],
    tmp_path: Path,
):
    """Test that installing an extension with an invalid manifest name fails."""
    bad_dir = _write_mock_extension(tmp_path / "bad-ext", name="bad_name")

    with pytest.raises(ValueError, match="Invalid extension name"):
        manager.install(str(bad_dir))


def test_install_force_preserves_enabled_state(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
):
    """Test that force reinstall preserves the existing enabled state."""
    manager.install(str(mock_extension_dir))
    manager.disable("mock-extension")

    info = manager.install(mock_extension_dir, force=True)

    assert info.enabled is False


# ============================================================================
# Uninstall Tests
# ============================================================================


def test_uninstall_existing_extension(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
    installation_dir: Path,
):
    """Test uninstalling an existing extension."""
    manager.install(str(mock_extension_dir))

    result = manager.uninstall("mock-extension")

    assert result is True
    assert not (installation_dir / "mock-extension").exists()

    metadata = InstallationMetadata.load_from_dir(installation_dir)
    assert "mock-extension" not in metadata.extensions


def test_uninstall_nonexistent_extension(
    manager: InstallationManager[MockExtension],
):
    """Test uninstalling an extension that doesn't exist."""
    result = manager.uninstall("nonexistent")
    assert result is False


def test_uninstall_untracked_extension_does_not_delete(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
    installation_dir: Path,
):
    """Test that uninstall refuses to delete untracked extension directories."""
    dest = installation_dir / "untracked-ext"
    shutil.copytree(mock_extension_dir, dest)

    # Rewrite the manifest so the name matches the directory
    _write_mock_extension(dest, name="untracked-ext")

    result = manager.uninstall("untracked-ext")

    assert result is False
    assert dest.exists()


def test_uninstall_tracked_but_directory_missing(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
    installation_dir: Path,
):
    """Test that uninstall succeeds when tracked but directory was already deleted."""
    manager.install(str(mock_extension_dir))
    shutil.rmtree(installation_dir / "mock-extension")

    result = manager.uninstall("mock-extension")

    assert result is True
    metadata = InstallationMetadata.load_from_dir(installation_dir)
    assert "mock-extension" not in metadata.extensions


def test_uninstall_invalid_name_raises_error(
    manager: InstallationManager[MockExtension],
):
    """Test that invalid extension names are rejected."""
    with pytest.raises(ValueError, match="Invalid extension name"):
        manager.uninstall("../evil")


# ============================================================================
# List Installed Tests
# ============================================================================


def test_list_nonexistent_installation_dir(tmp_path: Path):
    """Test listing when installation_dir doesn't exist returns empty."""
    manager = InstallationManager(
        installation_dir=tmp_path / "does-not-exist",
        installation_interface=MockExtensionInstallationInterface(),
    )
    assert manager.list_installed() == []


def test_list_empty_directory(
    manager: InstallationManager[MockExtension],
):
    """Test listing extensions from empty directory."""
    extensions = manager.list_installed()
    assert extensions == []


def test_list_installed_extensions(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
):
    """Test listing installed extensions."""
    manager.install(str(mock_extension_dir))

    extensions = manager.list_installed()

    assert len(extensions) == 1
    assert extensions[0].name == "mock-extension"
    assert extensions[0].version == "0.0.1"


def test_list_discovers_untracked_extensions(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
    installation_dir: Path,
):
    """Test that list discovers extensions not in metadata."""
    dest = installation_dir / "manual-ext"
    shutil.copytree(mock_extension_dir, dest)
    _write_mock_extension(dest, name="manual-ext")

    extensions = manager.list_installed()

    assert len(extensions) == 1
    assert extensions[0].name == "manual-ext"
    assert extensions[0].source == "local"


def test_list_cleans_up_missing_extensions(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
    installation_dir: Path,
):
    """Test that list removes metadata for missing extensions."""
    manager.install(str(mock_extension_dir))

    shutil.rmtree(installation_dir / "mock-extension")

    extensions = manager.list_installed()

    assert len(extensions) == 0
    metadata = InstallationMetadata.load_from_dir(installation_dir)
    assert "mock-extension" not in metadata.extensions


# ============================================================================
# Load Installed Tests
# ============================================================================


def test_load_nonexistent_installation_dir(tmp_path: Path):
    """Test loading when installation_dir doesn't exist returns empty."""
    manager = InstallationManager(
        installation_dir=tmp_path / "does-not-exist",
        installation_interface=MockExtensionInstallationInterface(),
    )
    assert manager.load_installed() == []


def test_load_empty_directory(
    manager: InstallationManager[MockExtension],
):
    """Test loading extensions from empty directory."""
    extensions = manager.load_installed()
    assert extensions == []


def test_load_installed_extensions(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
):
    """Test loading installed extensions."""
    manager.install(str(mock_extension_dir))

    extensions = manager.load_installed()

    assert len(extensions) == 1
    assert extensions[0].name == "mock-extension"


def test_disable_extension_filters_load(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
):
    """Test that disabled extensions are excluded from load."""
    manager.install(str(mock_extension_dir))

    assert manager.disable("mock-extension") is True

    extensions = manager.load_installed()
    assert extensions == []

    info = manager.get("mock-extension")
    assert info is not None
    assert info.enabled is False


def test_enable_extension_restores_load(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
):
    """Test that re-enabled extensions are loaded again."""
    manager.install(str(mock_extension_dir))
    manager.disable("mock-extension")

    assert manager.enable("mock-extension") is True

    extensions = manager.load_installed()
    assert len(extensions) == 1
    assert extensions[0].name == "mock-extension"


def test_enable_nonexistent_extension_returns_false(
    manager: InstallationManager[MockExtension],
):
    """Test that enabling a nonexistent extension returns False."""
    assert manager.enable("nonexistent") is False


def test_disable_nonexistent_extension_returns_false(
    manager: InstallationManager[MockExtension],
):
    """Test that disabling a nonexistent extension returns False."""
    assert manager.disable("nonexistent") is False


# ============================================================================
# Get Extension Tests
# ============================================================================


def test_get_existing_extension(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
):
    """Test getting info for an existing extension."""
    manager.install(str(mock_extension_dir))

    info = manager.get("mock-extension")

    assert info is not None
    assert info.name == "mock-extension"


def test_get_nonexistent_extension(
    manager: InstallationManager[MockExtension],
):
    """Test getting info for a nonexistent extension."""
    info = manager.get("nonexistent")
    assert info is None


def test_get_extension_with_missing_directory(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
    installation_dir: Path,
):
    """Test getting info when extension directory is missing."""
    manager.install(str(mock_extension_dir))

    shutil.rmtree(installation_dir / "mock-extension")

    info = manager.get("mock-extension")
    assert info is None


# ============================================================================
# Update Extension Tests
# ============================================================================


def test_update_existing_extension_local(
    manager: InstallationManager[MockExtension],
    mock_extension_dir: Path,
):
    """Test updating an installed extension from local source."""
    manager.install(str(mock_extension_dir))
    manager.disable("mock-extension")

    # Modify the source to a new version
    _write_mock_extension(
        mock_extension_dir,
        name="mock-extension",
        version="0.0.2",
        description="Updated extension",
    )

    updated = manager.update("mock-extension")

    assert updated is not None
    assert updated.version == "0.0.2"
    assert updated.enabled is False


def test_update_nonexistent_extension(
    manager: InstallationManager[MockExtension],
):
    """Test updating an extension that doesn't exist."""
    info = manager.update("nonexistent")
    assert info is None
