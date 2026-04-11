import json
from pathlib import Path

from pydantic import BaseModel

from openhands.sdk.extensions.installation import (
    InstallationInfo,
    InstallationInterface,
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


# ============================================================================
# Load / Save Tests
# ============================================================================


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


# ============================================================================
# validate_tracked Tests
# ============================================================================


def test_validate_tracked_prunes_invalid_names(tmp_path: Path):
    """Test that validate_tracked removes entries with invalid names."""
    installation_dir = tmp_path / "installed"
    installation_dir.mkdir()

    bad_info = InstallationInfo(
        name="Bad_Name",
        source="local",
        install_path=installation_dir / "Bad_Name",
    )
    good_info = InstallationInfo(
        name="good-ext",
        source="local",
        install_path=installation_dir / "good-ext",
    )
    (installation_dir / "good-ext").mkdir()

    metadata = InstallationMetadata(
        extensions={"Bad_Name": bad_info, "good-ext": good_info}
    )

    valid, changed = metadata.validate_tracked(installation_dir)

    assert changed is True
    assert len(valid) == 1
    assert valid[0].name == "good-ext"
    assert "Bad_Name" not in metadata.extensions


# ============================================================================
# discover_untracked Tests
# ============================================================================


def test_discover_untracked_skips_mismatched_manifest_name(tmp_path: Path):
    """Test that discover skips dirs where manifest name doesn't match dir name."""
    installation_dir = tmp_path / "installed"
    installation_dir.mkdir()

    # Create a dir named "some-ext" but manifest says "other-name"
    _write_mock_extension(installation_dir / "some-ext", name="other-name")

    metadata = InstallationMetadata()
    interface = MockExtensionInstallationInterface()

    discovered, changed = metadata.discover_untracked(installation_dir, interface)

    assert discovered == []
    assert changed is False
    assert "some-ext" not in metadata.extensions
