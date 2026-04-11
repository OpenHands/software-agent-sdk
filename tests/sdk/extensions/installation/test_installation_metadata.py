from pathlib import Path

from openhands.sdk.extensions.installation import InstallationInfo, InstallationMetadata


def test_load_from_dir_nonexistent(tmp_path: Path):
    """Test loading metadata from nonexistent directory returns empty."""
    metadata = InstallationMetadata.load_from_dir(tmp_path / "nonexistent")
    assert metadata.extensions == {}


def test_load_from_dir_and_save_to_dir(tmp_path: Path):
    """Test saving and loading metadata."""
    installation_dir = tmp_path / "installed"
    installation_dir.mkdir()

    info = InstallationInfo(
        name="test-extension",
        version="1.0.0",
        description="Test",
        source="github:owner/test",
        install_path=installation_dir / "test-extension",
    )

    metadata = InstallationMetadata(extensions={"test-extension": info})
    metadata.save_to_dir(installation_dir)

    loaded_metadata = InstallationMetadata.load_from_dir(installation_dir)

    assert metadata == loaded_metadata


def test_load_from_dir_invalid_json(tmp_path: Path):
    """Test loading invalid JSON returns empty metadata."""
    installation_dir = tmp_path / "installed"
    installation_dir.mkdir()

    metadata_path = InstallationMetadata.get_metadata_path(installation_dir)
    metadata_path.write_text("invalid json {")

    metadata = InstallationMetadata.load_from_dir(installation_dir)
    assert metadata.extensions == {}
