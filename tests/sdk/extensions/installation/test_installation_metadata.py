import logging
from pathlib import Path

import pytest
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
        return MockExtension.model_validate_json(
            (extension_dir / "extension.json").read_text()
        )


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
        f.write(ext.model_dump_json())
    return directory


# ============================================================================
# Legacy Key Migration Tests
# ============================================================================


def test_migrate_legacy_plugins_key():
    """Test that old {"plugins": {...}} format is migrated to extensions."""
    data = {
        "plugins": {
            "my-plugin": {
                "name": "my-plugin",
                "source": "github:owner/repo",
                "install_path": "/tmp/installed/my-plugin",
            }
        }
    }
    metadata = InstallationMetadata.model_validate(data)
    assert "my-plugin" in metadata.extensions
    assert metadata.extensions["my-plugin"].name == "my-plugin"


def test_migrate_legacy_skills_key():
    """Test that old {"skills": {...}} format is migrated to extensions."""
    data = {
        "skills": {
            "my-skill": {
                "name": "my-skill",
                "source": "local",
                "install_path": "/tmp/installed/my-skill",
                "enabled": False,
            }
        }
    }
    metadata = InstallationMetadata.model_validate(data)
    assert "my-skill" in metadata.extensions
    assert metadata.extensions["my-skill"].enabled is False


def test_migrate_merges_both_legacy_keys():
    """Test that both plugins and skills are merged when both are present."""
    data = {
        "plugins": {
            "my-plugin": {
                "name": "my-plugin",
                "source": "github:owner/repo",
                "install_path": "/tmp/installed/my-plugin",
            }
        },
        "skills": {
            "my-skill": {
                "name": "my-skill",
                "source": "local",
                "install_path": "/tmp/installed/my-skill",
            }
        },
    }
    metadata = InstallationMetadata.model_validate(data)
    assert "my-plugin" in metadata.extensions
    assert "my-skill" in metadata.extensions


def test_migrate_legacy_key_logs_warning(caplog: pytest.LogCaptureFixture):
    """Each legacy key that is migrated emits a warning."""
    data = {
        "plugins": {
            "p": {
                "name": "p",
                "source": "local",
                "install_path": "/tmp/p",
            }
        },
        "skills": {
            "s": {
                "name": "s",
                "source": "local",
                "install_path": "/tmp/s",
            }
        },
    }
    with caplog.at_level(logging.WARNING):
        InstallationMetadata.model_validate(data)

    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("plugins" in w for w in warnings)
    assert any("skills" in w for w in warnings)


def test_migrate_merges_legacy_into_extensions():
    """Legacy keys are merged into extensions; extensions wins on conflicts."""
    data = {
        "extensions": {
            "new-ext": {
                "name": "new-ext",
                "source": "local",
                "install_path": "/tmp/installed/new-ext",
            }
        },
        "plugins": {
            "old-plugin": {
                "name": "old-plugin",
                "source": "local",
                "install_path": "/tmp/installed/old-plugin",
            }
        },
    }
    metadata = InstallationMetadata.model_validate(data)
    assert "new-ext" in metadata.extensions
    assert "old-plugin" in metadata.extensions


def test_migrate_extensions_wins_on_conflict():
    """When a name appears in both extensions and a legacy key, extensions wins."""
    data = {
        "extensions": {
            "shared": {
                "name": "shared",
                "source": "local",
                "install_path": "/tmp/installed/shared",
            }
        },
        "plugins": {
            "shared": {
                "name": "shared",
                "source": "github:owner/repo",
                "install_path": "/tmp/installed/shared",
            }
        },
    }
    metadata = InstallationMetadata.model_validate(data)
    assert metadata.extensions["shared"].source == "local"


def test_migrate_conflicting_legacy_keys():
    """When both plugins and skills have the same name, the later key wins."""
    data = {
        "plugins": {
            "shared": {
                "name": "shared",
                "source": "github:A",
                "install_path": "/tmp/installed/shared",
            }
        },
        "skills": {
            "shared": {
                "name": "shared",
                "source": "github:B",
                "install_path": "/tmp/installed/shared",
            }
        },
    }
    metadata = InstallationMetadata.model_validate(data)
    # skills is iterated after plugins in _LEGACY_KEYS, so it overwrites
    assert metadata.extensions["shared"].source == "github:B"


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
# open() Context Manager Tests
# ============================================================================


def test_open_saves_on_clean_exit(tmp_path: Path):
    """Test that the context manager auto-saves on a clean exit."""
    installation_dir = tmp_path / "installed"
    installation_dir.mkdir()

    info = InstallationInfo(
        name="test-ext",
        source="local",
        install_path=installation_dir / "test-ext",
    )

    with InstallationMetadata.open(installation_dir) as session:
        session.extensions["test-ext"] = info

    loaded = InstallationMetadata.load_from_dir(installation_dir)
    assert "test-ext" in loaded.extensions


def test_open_does_not_save_on_exception(tmp_path: Path):
    """Test that the context manager does not save when an exception occurs."""
    installation_dir = tmp_path / "installed"
    installation_dir.mkdir()

    info = InstallationInfo(
        name="test-ext",
        source="local",
        install_path=installation_dir / "test-ext",
    )

    try:
        with InstallationMetadata.open(installation_dir) as session:
            session.extensions["test-ext"] = info
            raise RuntimeError("simulated failure")
    except RuntimeError:
        pass

    loaded = InstallationMetadata.load_from_dir(installation_dir)
    assert loaded.extensions == {}


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

    valid = metadata.validate_tracked(installation_dir)

    assert len(valid) == 1
    assert valid[0].name == "good-ext"
    assert "Bad_Name" not in metadata.extensions


# ============================================================================
# discover_untracked Tests
# ============================================================================


def test_discover_untracked_skips_mismatched_manifest_name(tmp_path: Path):
    """Test that discover skips dirs where manifest name doesn't match."""
    installation_dir = tmp_path / "installed"
    installation_dir.mkdir()

    _write_mock_extension(installation_dir / "some-ext", name="other-name")

    metadata = InstallationMetadata()
    interface = MockExtensionInstallationInterface()

    discovered = metadata.discover_untracked(installation_dir, interface)

    assert discovered == []
    assert "some-ext" not in metadata.extensions
