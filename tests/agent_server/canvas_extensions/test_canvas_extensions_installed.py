"""Tests for canvas extension installation persistence.

Covers the disabled-by-default regression scenarios (fresh install,
smuggled ``enabled: true`` in stale metadata, manually-placed directory
discovery), plus force-reinstall state preservation and entrypoint
containment enforced at load time.
"""

import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from openhands.agent_server.canvas_extensions.installed import (
    disable_canvas_extension,
    enable_canvas_extension,
    get_installed_canvas_extension,
    get_installed_canvas_extensions_dir,
    install_canvas_extension,
    list_installed_canvas_extensions,
    load_installed_canvas_extensions,
    uninstall_canvas_extension,
)
from openhands.agent_server.canvas_extensions.manifest import MANIFEST_FILENAME
from openhands.sdk.extensions.installation import InstallationMetadata

from .conftest import write_extension as _write_extension


def test_default_installed_dir_layout():
    parts = get_installed_canvas_extensions_dir().parts
    assert parts[-3:] == (".openhands", "canvas-extensions", "installed")


def test_fresh_install_lands_disabled(extension_dir: Path, installed_dir: Path):
    info = install_canvas_extension(str(extension_dir), installed_dir=installed_dir)

    assert info.enabled is False

    # Not just the in-memory return value -- persisted to disk too.
    on_disk = InstallationMetadata.load_from_dir(installed_dir)
    assert on_disk.extensions["my-extension"].enabled is False


def test_fresh_install_disabled_excludes_from_load(
    extension_dir: Path, installed_dir: Path
):
    install_canvas_extension(str(extension_dir), installed_dir=installed_dir)

    loaded = load_installed_canvas_extensions(installed_dir=installed_dir)

    assert loaded == []


def test_explicit_enable_after_install_makes_it_load(
    extension_dir: Path, installed_dir: Path
):
    install_canvas_extension(str(extension_dir), installed_dir=installed_dir)

    assert enable_canvas_extension("my-extension", installed_dir=installed_dir) is True

    loaded = load_installed_canvas_extensions(installed_dir=installed_dir)
    assert [m.name for m in loaded] == ["my-extension"]


def test_smuggled_enabled_true_in_stale_metadata_is_ignored(
    extension_dir: Path, installed_dir: Path
):
    """Otherwise a real install of that name would inherit it as if it
    were a legitimate force-reinstall.
    """
    installed_dir.mkdir(parents=True)
    (installed_dir / InstallationMetadata.metadata_filename).write_text(
        json.dumps(
            {
                "extensions": {
                    "my-extension": {
                        "name": "my-extension",
                        "version": "0.0.0",
                        "description": "",
                        "enabled": True,
                        "source": "local",
                        "install_path": str(installed_dir / "my-extension"),
                    }
                }
            }
        )
    )
    assert not (installed_dir / "my-extension").exists()

    info = install_canvas_extension(str(extension_dir), installed_dir=installed_dir)

    assert info.enabled is False
    on_disk = InstallationMetadata.load_from_dir(installed_dir)
    assert on_disk.extensions["my-extension"].enabled is False


def test_install_ignores_unexpected_kwargs():
    """No ``enabled`` parameter exists to pass one through, smuggled or not."""
    params = inspect.signature(install_canvas_extension).parameters
    assert "enabled" not in params


def test_manually_placed_directory_discovered_disabled(
    extension_dir: Path, installed_dir: Path
):
    installed_dir.mkdir(parents=True)
    manual = installed_dir / "manual-ext"
    _write_extension(manual, name="manual-ext")
    # No .installed.json entry at all -- fully bypasses the install API.
    assert not (installed_dir / InstallationMetadata.metadata_filename).exists()

    discovered = list_installed_canvas_extensions(installed_dir=installed_dir)

    assert len(discovered) == 1
    assert discovered[0].name == "manual-ext"
    assert discovered[0].enabled is False

    # And it stays disabled on a subsequent get(), reading persisted state.
    info = get_installed_canvas_extension("manual-ext", installed_dir=installed_dir)
    assert info is not None
    assert info.enabled is False


def test_manually_placed_directory_discovered_disabled_excludes_from_load(
    installed_dir: Path,
):
    """load_installed_canvas_extensions() must apply the same
    disabled-by-default guarantee as list_installed_canvas_extensions() --
    discovery here must not leave the extension loadable.
    """
    installed_dir.mkdir(parents=True)
    manual = installed_dir / "manual-ext"
    _write_extension(manual, name="manual-ext")
    assert not (installed_dir / InstallationMetadata.metadata_filename).exists()

    loaded = load_installed_canvas_extensions(installed_dir=installed_dir)

    assert loaded == []
    on_disk = InstallationMetadata.load_from_dir(installed_dir)
    assert on_disk.extensions["manual-ext"].enabled is False


def test_previously_enabled_tracked_extension_not_reset_by_listing(
    extension_dir: Path, installed_dir: Path
):
    install_canvas_extension(str(extension_dir), installed_dir=installed_dir)
    enable_canvas_extension("my-extension", installed_dir=installed_dir)

    infos = list_installed_canvas_extensions(installed_dir=installed_dir)

    assert len(infos) == 1
    assert infos[0].enabled is True


def test_list_handles_mixed_states_across_multiple_extensions(
    tmp_path: Path, installed_dir: Path
):
    """The per-entry force-disable correction must not cross-contaminate
    sibling entries within the same listing call.
    """
    enabled_src = _write_extension(tmp_path / "source" / "enabled", name="enabled-ext")
    disabled_src = _write_extension(
        tmp_path / "source" / "disabled", name="disabled-ext"
    )
    install_canvas_extension(str(enabled_src), installed_dir=installed_dir)
    enable_canvas_extension("enabled-ext", installed_dir=installed_dir)
    install_canvas_extension(str(disabled_src), installed_dir=installed_dir)
    _write_extension(installed_dir / "manual-ext", name="manual-ext")

    states = {
        info.name: info.enabled
        for info in list_installed_canvas_extensions(installed_dir=installed_dir)
    }

    assert states == {
        "enabled-ext": True,
        "disabled-ext": False,
        "manual-ext": False,
    }


def test_list_tolerates_invalid_name_in_stale_metadata(installed_dir: Path):
    installed_dir.mkdir(parents=True)
    (installed_dir / InstallationMetadata.metadata_filename).write_text(
        json.dumps(
            {
                "extensions": {
                    "Bad_Name": {
                        "name": "Bad_Name",
                        "version": "0.0.0",
                        "description": "",
                        "enabled": True,
                        "source": "local",
                        "install_path": str(installed_dir / "Bad_Name"),
                    }
                }
            }
        )
    )

    infos = list_installed_canvas_extensions(installed_dir=installed_dir)

    assert infos == []
    on_disk = InstallationMetadata.load_from_dir(installed_dir)
    assert "Bad_Name" not in on_disk.extensions


def test_list_empty_installed_dir_returns_empty(installed_dir: Path):
    assert list_installed_canvas_extensions(installed_dir=installed_dir) == []


def test_list_nonexistent_installed_dir_returns_empty(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    assert list_installed_canvas_extensions(installed_dir=missing) == []


def test_force_reinstall_preserves_enabled_state(
    extension_dir: Path, installed_dir: Path
):
    install_canvas_extension(str(extension_dir), installed_dir=installed_dir)
    enable_canvas_extension("my-extension", installed_dir=installed_dir)

    info = install_canvas_extension(
        str(extension_dir), installed_dir=installed_dir, force=True
    )

    assert info.enabled is True


def test_force_reinstall_preserves_disabled_state(
    extension_dir: Path, installed_dir: Path
):
    install_canvas_extension(str(extension_dir), installed_dir=installed_dir)

    info = install_canvas_extension(
        str(extension_dir), installed_dir=installed_dir, force=True
    )

    assert info.enabled is False


def test_install_without_force_raises_when_already_installed(
    extension_dir: Path, installed_dir: Path
):
    install_canvas_extension(str(extension_dir), installed_dir=installed_dir)
    enable_canvas_extension("my-extension", installed_dir=installed_dir)

    with pytest.raises(FileExistsError):
        install_canvas_extension(str(extension_dir), installed_dir=installed_dir)

    # The existing install is untouched by the rejected attempt.
    info = get_installed_canvas_extension("my-extension", installed_dir=installed_dir)
    assert info is not None
    assert info.enabled is True


def test_install_rejects_manifest_with_escaping_entrypoint(
    tmp_path: Path, installed_dir: Path
):
    outside = tmp_path / "outside.js"
    outside.write_text("payload")

    malicious = tmp_path / "source" / "evil-extension"
    malicious.mkdir(parents=True)
    (malicious / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "evil-extension",
                "display_name": "Evil",
                "version": "1.0.0",
                "entrypoint": "escape.js",
            }
        )
    )
    (malicious / "escape.js").symlink_to(outside)

    with pytest.raises(ValueError, match="resolves outside"):
        install_canvas_extension(str(malicious), installed_dir=installed_dir)

    # Rejected before anything was tracked or copied.
    assert not (installed_dir / "evil-extension").exists()
    assert list_installed_canvas_extensions(installed_dir=installed_dir) == []


def test_discovery_skips_directory_with_escaping_entrypoint(
    tmp_path: Path, installed_dir: Path
):
    installed_dir.mkdir(parents=True)
    outside = tmp_path / "outside.js"
    outside.write_text("payload")

    manual = installed_dir / "evil-extension"
    manual.mkdir(parents=True)
    (manual / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "evil-extension",
                "display_name": "Evil",
                "version": "1.0.0",
                "entrypoint": "escape.js",
            }
        )
    )
    (manual / "escape.js").symlink_to(outside)

    discovered = list_installed_canvas_extensions(installed_dir=installed_dir)

    assert discovered == []
    on_disk = InstallationMetadata.load_from_dir(installed_dir)
    assert "evil-extension" not in on_disk.extensions


@pytest.mark.parametrize(
    "drop_field,name_override",
    [
        pytest.param("entrypoint", None, id="missing-required-field"),
        pytest.param(None, "Bad_Name", id="invalid-name"),
    ],
)
def test_install_rejects_invalid_manifest(
    tmp_path: Path,
    installed_dir: Path,
    drop_field: str | None,
    name_override: str | None,
):
    payload: dict[str, Any] = {
        "schema_version": 1,
        "name": name_override or "bad-extension",
        "display_name": "Bad",
        "version": "1.0.0",
        "entrypoint": "dist/index.js",
    }
    if drop_field:
        del payload[drop_field]
    bad = tmp_path / "source" / "bad-extension"
    bad.mkdir(parents=True)
    (bad / MANIFEST_FILENAME).write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        install_canvas_extension(str(bad), installed_dir=installed_dir)

    assert list_installed_canvas_extensions(installed_dir=installed_dir) == []


def test_discovery_skips_directory_without_manifest_file(installed_dir: Path):
    installed_dir.mkdir(parents=True)
    (installed_dir / "no-manifest").mkdir()
    (installed_dir / "no-manifest" / "random.txt").write_text("not a manifest")

    discovered = list_installed_canvas_extensions(installed_dir=installed_dir)

    assert discovered == []
    on_disk = InstallationMetadata.load_from_dir(installed_dir)
    assert "no-manifest" not in on_disk.extensions


def test_discovery_skips_directory_with_mismatched_manifest_name(installed_dir: Path):
    installed_dir.mkdir(parents=True)
    _write_extension(installed_dir / "dir-name", name="manifest-name")

    discovered = list_installed_canvas_extensions(installed_dir=installed_dir)

    assert discovered == []
    on_disk = InstallationMetadata.load_from_dir(installed_dir)
    assert "dir-name" not in on_disk.extensions
    assert "manifest-name" not in on_disk.extensions


def test_uninstall_removes_tracked_extension(extension_dir: Path, installed_dir: Path):
    install_canvas_extension(str(extension_dir), installed_dir=installed_dir)

    assert (
        uninstall_canvas_extension("my-extension", installed_dir=installed_dir) is True
    )
    assert not (installed_dir / "my-extension").exists()
    assert (
        get_installed_canvas_extension("my-extension", installed_dir=installed_dir)
        is None
    )


def test_uninstall_untracked_extension_returns_false(installed_dir: Path):
    assert (
        uninstall_canvas_extension("nonexistent", installed_dir=installed_dir) is False
    )


def test_disable_nonexistent_extension_returns_false(installed_dir: Path):
    assert disable_canvas_extension("nonexistent", installed_dir=installed_dir) is False


def test_enable_nonexistent_extension_returns_false(installed_dir: Path):
    assert enable_canvas_extension("nonexistent", installed_dir=installed_dir) is False
