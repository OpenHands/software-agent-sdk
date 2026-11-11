import logging
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from openhands.sdk.extensions.installation import (
    InstallationInfo,
    InstallationInterface,
    InstallationMetadata,
    InstallationMetadataLoadError,
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


def test_save_and_load_round_trip_non_ascii(tmp_path: Path):
    """save_to_dir() then load_from_dir() must round-trip non-ASCII content.

    save_to_dir()/load_from_dir() use Path.write_text()/read_text(), which
    fall back to the platform default encoding (cp1252 on Windows) when no
    encoding is given. cp1252 cannot represent characters like emoji, so
    metadata with non-ASCII fields would fail to persist. Both ends must
    use UTF-8.
    """
    installation_dir = tmp_path / "installed"
    installation_dir.mkdir()

    info = InstallationInfo(
        name="café-extension",
        version="1.0.0",
        description="Adds 🔐 secure access review — supports français",
        source="github:owner/café-extension",
        install_path=installation_dir / "café-extension",
    )
    metadata = InstallationMetadata(extensions={"café-extension": info})
    metadata.save_to_dir(installation_dir)

    # The metadata file on disk must be valid UTF-8.
    metadata_path = InstallationMetadata.get_metadata_path(installation_dir)
    assert "🔐" in metadata_path.read_bytes().decode("utf-8")

    loaded_metadata = InstallationMetadata.load_from_dir(installation_dir)
    assert metadata == loaded_metadata
    assert loaded_metadata.extensions["café-extension"].description == (
        "Adds 🔐 secure access review — supports français"
    )


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


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"invalid json {", id="invalid-json"),
        pytest.param(b'{"extensions": []}', id="wrong-field-type"),
        pytest.param(b"[]", id="wrong-top-level-type"),
        pytest.param(b"\xff\xfe not utf-8", id="not-utf-8"),
    ],
)
def test_open_refuses_metadata_it_cannot_load(tmp_path: Path, content: bytes):
    """A write session must not save its empty fallback over a broken file."""
    installation_dir = tmp_path / "installed"
    _write_mock_extension(installation_dir / "mock-extension")
    metadata_path = InstallationMetadata.get_metadata_path(installation_dir)
    metadata_path.write_bytes(content)

    with pytest.raises(InstallationMetadataLoadError) as exc_info:
        with InstallationMetadata.open(
            installation_dir, interface=MockExtensionInstallationInterface()
        ) as session:
            session.sync()

    assert exc_info.value.metadata_path == metadata_path
    assert str(metadata_path) in str(exc_info.value)
    assert metadata_path.read_bytes() == content


def test_open_without_metadata_file_starts_empty(tmp_path: Path):
    """A missing file is a fresh install, not an error."""
    installation_dir = tmp_path / "installed"
    _write_mock_extension(installation_dir / "mock-extension")

    with InstallationMetadata.open(
        installation_dir, interface=MockExtensionInstallationInterface()
    ) as session:
        assert session.extensions == {}
        session.sync()

    loaded = InstallationMetadata.load_from_dir(installation_dir)
    assert "mock-extension" in loaded.extensions


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: -1)() == 0,
    reason="root bypasses file permissions",
)
def test_open_refuses_unreadable_metadata(tmp_path: Path):
    """An unreadable file raises instead of being replaced by an atomic save."""
    installation_dir = tmp_path / "installed"
    _write_mock_extension(installation_dir / "mock-extension")
    metadata_path = InstallationMetadata.get_metadata_path(installation_dir)
    content = b'{"extensions": {}}'
    metadata_path.write_bytes(content)
    metadata_path.chmod(0)
    try:
        with pytest.raises(InstallationMetadataLoadError):
            with InstallationMetadata.open(
                installation_dir, interface=MockExtensionInstallationInterface()
            ) as session:
                session.sync()
    finally:
        metadata_path.chmod(0o644)

    assert metadata_path.read_bytes() == content


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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.parametrize("existing_mode", [0o600, 0o644], ids=oct)
def test_save_to_dir_keeps_existing_file_mode(tmp_path: Path, existing_mode: int):
    """A save keeps the file's mode whatever the umask: an owner-only file stays
    owner-only (sources can carry URL credentials), and a file other local users
    read is not narrowed under them."""
    metadata_path = InstallationMetadata.get_metadata_path(tmp_path)
    metadata_path.write_text("{}")
    metadata_path.chmod(existing_mode)
    previous_umask = os.umask(0o027)
    try:
        InstallationMetadata().save_to_dir(tmp_path)
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(metadata_path.stat().st_mode) == existing_mode


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.parametrize(
    ("umask", "expected"),
    [
        pytest.param(0o022, 0o644, id="umask-022"),
        pytest.param(0o077, 0o600, id="umask-077"),
    ],
)
def test_save_to_dir_creates_new_file_with_umask_mode(
    tmp_path: Path, umask: int, expected: int
):
    """A new file gets the umask's mode, as a plain write would give it: a
    strict umask keeps sources, which can carry URL credentials, owner-only."""
    metadata_path = InstallationMetadata.get_metadata_path(tmp_path)
    previous_umask = os.umask(umask)
    try:
        InstallationMetadata().save_to_dir(tmp_path)
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(metadata_path.stat().st_mode) == expected


# A uid and gid that are not root's: the user a root process (an agent-server
# container) shares an installation directory with.
_USER = 65534

_requires_root = pytest.mark.skipif(
    getattr(os, "geteuid", lambda: -1)() != 0, reason="changing owners needs root"
)


@_requires_root
def test_root_session_keeps_the_directory_users_ownership(tmp_path: Path):
    """When root saves in a user's installation directory, that user can still
    read the metadata file and take the lock afterwards."""
    installation_dir = tmp_path / "installed"
    _write_mock_extension(installation_dir / "mock-extension")
    shutil.chown(installation_dir, _USER, _USER)
    metadata_path = InstallationMetadata.get_metadata_path(installation_dir)
    metadata_path.write_text('{"extensions": {}}')
    metadata_path.chmod(0o600)
    shutil.chown(metadata_path, _USER, _USER)

    with InstallationMetadata.open(
        installation_dir, interface=MockExtensionInstallationInterface()
    ) as session:
        session.sync()

    for path in (metadata_path, installation_dir / ".metadata.lock"):
        assert (path.stat().st_uid, path.stat().st_gid) == (_USER, _USER), path
    assert stat.S_IMODE(metadata_path.stat().st_mode) == 0o600


@_requires_root
def test_root_session_does_not_give_away_a_hard_linked_lock_file(tmp_path: Path):
    """A lock file that is another file's second name is left alone: the
    directory's owner must not be able to get that file through root."""
    installation_dir = tmp_path / "installed"
    installation_dir.mkdir()
    shutil.chown(installation_dir, _USER, _USER)
    other_file = tmp_path / "root-owned"
    other_file.write_text("x")
    os.link(other_file, installation_dir / ".metadata.lock")

    with InstallationMetadata.open(
        installation_dir, interface=MockExtensionInstallationInterface()
    ):
        pass

    assert other_file.stat().st_uid == 0
