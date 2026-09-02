"""Installed Canvas Extensions.

Built on ``openhands.sdk.extensions.installation``, shared with Plugins/
Skills, with two behaviors specific to this module:

Disabled by default -- ``InstallationInfo.enabled`` defaults to ``True``
and neither ``InstallationManager.install()`` nor its directory discovery
override that for a new entry, so every write path that can create one
forces ``enabled=False`` as an explicit post-write step.

Staged refresh -- ``check``/``apply`` replace ``update()``/
``install(force=True)``, which rmtree+copytree straight onto the live
install path with no staging directory. ``check`` fetches and validates
into ``.staging/`` without touching the active install; ``apply`` swaps it
in via two atomic renames (POSIX can't atomically replace a non-empty
directory in one rename), rolling back if the second fails.
"""

import os
import shutil
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from openhands.agent_server.canvas_extensions.manifest import (
    MANIFEST_FILENAME,
    CanvasExtensionManifest,
    resolve_entrypoint,
)
from openhands.sdk.extensions.fetch import fetch_with_resolution
from openhands.sdk.extensions.installation import (
    InstallationInfo,
    InstallationInterface,
    InstallationManager,
    InstallationMetadata,
)
from openhands.sdk.extensions.installation.manager import DEFAULT_CACHE_DIR


# Matches the InstalledPluginInfo naming convention.
InstalledCanvasExtensionInfo = InstallationInfo


def get_installed_canvas_extensions_dir() -> Path:
    """Get the default directory for installed canvas extensions."""
    return Path.home() / ".openhands" / "canvas-extensions" / "installed"


class CanvasExtensionInstallationInterface(
    InstallationInterface[CanvasExtensionManifest]
):
    @staticmethod
    def load_from_dir(extension_dir: Path) -> CanvasExtensionManifest:
        manifest_path = extension_dir / MANIFEST_FILENAME
        manifest = CanvasExtensionManifest.model_validate_json(
            manifest_path.read_text()
        )
        # Containment must hold too -- a parseable manifest alone isn't enough.
        resolve_entrypoint(manifest, extension_dir)
        return manifest


def _resolve_installed_dir(installed_dir: Path | None) -> Path:
    return (
        installed_dir
        if installed_dir is not None
        else get_installed_canvas_extensions_dir()
    )


def _manager(installed_dir: Path) -> InstallationManager[CanvasExtensionManifest]:
    return InstallationManager(
        installation_dir=installed_dir,
        installation_interface=CanvasExtensionInstallationInterface(),
    )


def _tracked_names(installed_dir: Path) -> set[str]:
    """Names with a real, valid tracked entry, not just a metadata key.

    A stale record with no matching directory must not count as "already
    installed" -- otherwise a real install of that name would wrongly
    inherit its state as if this were a force-reinstall.
    """
    if not installed_dir.exists():
        return set()
    metadata = InstallationMetadata.load_from_dir(installed_dir)
    return {info.name for info in metadata.validate_tracked(installed_dir)}


def _force_disable_new(
    manager: InstallationManager[CanvasExtensionManifest],
    info: InstallationInfo,
    pre_existing: set[str],
) -> InstallationInfo:
    """Force a newly created tracked entry to ``enabled=False``.

    ``pre_existing`` distinguishes a genuinely new entry from a
    force-reinstall, whose prior enabled state is already preserved.
    """
    if info.name in pre_existing or not info.enabled:
        return info
    manager.disable(info.name)
    info.enabled = False
    return info


def install_canvas_extension(
    source: str,
    ref: str | None = None,
    repo_path: str | None = None,
    installed_dir: Path | None = None,
    force: bool = False,
) -> InstalledCanvasExtensionInfo:
    """Install a canvas extension from a source.

    A newly created entry always lands disabled, regardless of what the
    caller passes in.

    Args:
        source: ``"github:owner/repo"``, a git URL, or a local path.
        ref: Optional branch, tag, or commit to install.
        repo_path: Subdirectory within the repository (for monorepos).
        installed_dir: Defaults to ``~/.openhands/canvas-extensions/installed/``.
        force: If True, overwrite an existing installation.

    Returns:
        InstalledCanvasExtensionInfo for the installed extension.
    """
    installed_dir = _resolve_installed_dir(installed_dir)
    manager = _manager(installed_dir)
    pre_existing = _tracked_names(installed_dir)
    info = manager.install(source, ref=ref, repo_path=repo_path, force=force)
    return _force_disable_new(manager, info, pre_existing)


def uninstall_canvas_extension(name: str, installed_dir: Path | None = None) -> bool:
    """Uninstall a canvas extension by name.

    Also drops any staged, not-yet-applied update for *name*, so refreshing
    a since-uninstalled name never leaves an orphaned staging slot behind.

    Returns:
        True if uninstalled, False if it wasn't installed.
    """
    installed_dir = _resolve_installed_dir(installed_dir)
    uninstalled = _manager(installed_dir).uninstall(name)
    if uninstalled:
        shutil.rmtree(_staging_root(installed_dir) / name, ignore_errors=True)
    return uninstalled


def enable_canvas_extension(name: str, installed_dir: Path | None = None) -> bool:
    """Enable an installed canvas extension by name."""
    return _manager(_resolve_installed_dir(installed_dir)).enable(name)


def disable_canvas_extension(name: str, installed_dir: Path | None = None) -> bool:
    """Disable an installed canvas extension by name."""
    return _manager(_resolve_installed_dir(installed_dir)).disable(name)


def list_installed_canvas_extensions(
    installed_dir: Path | None = None,
) -> list[InstalledCanvasExtensionInfo]:
    """List all installed canvas extensions.

    Self-healing like ``InstallationManager.list_installed()``; directories
    discovered this way also land disabled.
    """
    installed_dir = _resolve_installed_dir(installed_dir)
    manager = _manager(installed_dir)
    pre_existing = _tracked_names(installed_dir)
    infos = manager.list_installed()
    return [_force_disable_new(manager, info, pre_existing) for info in infos]


def load_installed_canvas_extensions(
    installed_dir: Path | None = None,
) -> list[CanvasExtensionManifest]:
    """Load all enabled canvas extensions' manifests.

    Runs through ``list_installed_canvas_extensions`` first so discovery
    is force-disabled before anything is loaded -- see the module
    docstring. Mirrors ``InstallationManager.load_installed()``'s own
    enabled-filter/load logic on top of the corrected info list.
    """
    installed_dir = _resolve_installed_dir(installed_dir)
    manager = _manager(installed_dir)
    manifests: list[CanvasExtensionManifest] = []
    for info in list_installed_canvas_extensions(installed_dir):
        if not info.enabled:
            continue
        extension_path = installed_dir / info.name
        if extension_path.exists():
            manifests.append(
                manager.installation_interface.load_from_dir(extension_path)
            )
    return manifests


def get_installed_canvas_extension(
    name: str, installed_dir: Path | None = None
) -> InstalledCanvasExtensionInfo | None:
    """Get information about a specific installed canvas extension."""
    return _manager(_resolve_installed_dir(installed_dir)).get(name)


def get_installed_canvas_extension_manifest(
    name: str, installed_dir: Path | None = None
) -> CanvasExtensionManifest | None:
    """Load the validated manifest for an installed extension.

    Re-reads and re-validates the stored manifest against the live install
    path -- the copy validated at install time may have changed on disk
    since.

    Returns:
        None if not installed, or if the install no longer validates.
    """
    installed_dir = _resolve_installed_dir(installed_dir)
    if get_installed_canvas_extension(name, installed_dir) is None:
        return None
    try:
        return CanvasExtensionInstallationInterface.load_from_dir(installed_dir / name)
    except (ValidationError, ValueError, OSError):
        return None


def get_canvas_extension_bundle_path(
    name: str, installed_dir: Path | None = None
) -> Path | None:
    """Resolve the on-disk path to *name*'s entrypoint bundle file.

    Re-validates the manifest and entrypoint containment against the live
    install path on every call -- this backs a bundle serve, so the check
    done at install time isn't enough on its own (a symlink could change
    between requests).

    Returns:
        None if not installed, or if the install no longer validates.
    """
    installed_dir = _resolve_installed_dir(installed_dir)
    manifest = get_installed_canvas_extension_manifest(name, installed_dir)
    if manifest is None:
        return None
    return resolve_entrypoint(manifest, installed_dir / name)


class CanvasExtensionUpdateCheck(BaseModel):
    """Result of ``check_canvas_extension_update``.

    The active install is untouched; only ``.staging/`` is written. Pass
    ``resolved_ref`` back to ``apply_canvas_extension_update`` to confirm
    and swap this exact staged content into place.
    """

    requested_ref: str | None = Field(
        description="Ref the staged fetch was resolved against"
    )
    resolved_ref: str | None = Field(
        description="Commit SHA the staged content was resolved to"
    )
    validated: bool = Field(
        description="Whether staged content passed validation; only True may be applied"
    )


def _staging_root(installed_dir: Path) -> Path:
    return installed_dir / ".staging"


def _staged_path(installed_dir: Path, name: str, resolved_ref: str | None) -> Path:
    """Path to the staging slot for (*name*, *resolved_ref*).

    *resolved_ref* is untrusted (``apply_canvas_extension_update`` takes it
    as a caller-supplied argument), so it's rejected if it could escape the
    staging directory -- same check as ``entrypoint`` in manifest.py.
    """
    if resolved_ref is not None and (
        not resolved_ref
        or resolved_ref.startswith("/")
        or ".." in Path(resolved_ref).parts
    ):
        raise ValueError(f"Invalid resolved_ref: {resolved_ref!r}")
    return _staging_root(installed_dir) / name / (resolved_ref or "local")


def _stage_fetched_content(
    installed_dir: Path, name: str, resolved_ref: str | None, fetched_path: Path
) -> Path:
    """Copy fetched content into a clean staging slot for *name*.

    Clears any previously staged candidate first -- at most one is kept
    per extension at a time.
    """
    slot_root = _staging_root(installed_dir) / name
    shutil.rmtree(slot_root, ignore_errors=True)
    staged_path = _staged_path(installed_dir, name, resolved_ref)
    # symlinks=True: dereferencing here would copy a malicious symlink's
    # target in as a plain file, defeating containment validation below.
    shutil.copytree(fetched_path, staged_path, symlinks=True)
    return staged_path


def _load_validated_manifest(
    staged_path: Path, expected_name: str
) -> CanvasExtensionManifest:
    """Load and validate a staged extension's manifest.

    Raises if the manifest is malformed, its entrypoint escapes the package
    root, or its name no longer matches *expected_name*.
    """
    manifest = CanvasExtensionInstallationInterface.load_from_dir(staged_path)
    if manifest.name != expected_name:
        raise ValueError(
            f"Staged content for {expected_name!r} declares a different "
            f"name {manifest.name!r}; refusing to treat it as an update"
        )
    return manifest


def check_canvas_extension_update(
    name: str, installed_dir: Path | None = None
) -> CanvasExtensionUpdateCheck | None:
    """Check for an update to *name*, staging and validating it.

    Re-fetches the tracked source at its original ref (never "latest")
    into ``.staging/``; the active install is untouched. See
    ``apply_canvas_extension_update`` for the step that swaps it into
    place.

    Args:
        name: Name of the installed extension to check.
        installed_dir: Defaults to ``~/.openhands/canvas-extensions/installed/``.

    Returns:
        None if not installed, else a result -- only apply when ``validated``.

    Raises:
        ValueError: If *name* is not valid kebab-case.
        ExtensionFetchError: If fetching the tracked source fails.
    """
    installed_dir = _resolve_installed_dir(installed_dir)
    manager = _manager(installed_dir)
    current_info = manager.get(name)
    if current_info is None:
        return None

    fetched_path, resolved_ref = fetch_with_resolution(
        source=current_info.source,
        cache_dir=DEFAULT_CACHE_DIR,
        ref=current_info.requested_ref,
        repo_path=current_info.repo_path,
        update=True,
    )

    staged_path = _stage_fetched_content(
        installed_dir, name, resolved_ref, fetched_path
    )
    try:
        _load_validated_manifest(staged_path, name)
        validated = True
    except (ValidationError, ValueError, OSError):
        validated = False
        shutil.rmtree(staged_path.parent, ignore_errors=True)

    return CanvasExtensionUpdateCheck(
        requested_ref=current_info.requested_ref,
        resolved_ref=resolved_ref,
        validated=validated,
    )


def apply_canvas_extension_update(
    name: str,
    resolved_ref: str | None,
    enabled: bool,
    installed_dir: Path | None = None,
) -> InstalledCanvasExtensionInfo | None:
    """Apply a previously checked and validated update.

    *resolved_ref* must match a staged candidate already validated by
    ``check_canvas_extension_update``, reconfirming what's being applied.
    Atomically swaps staged content into the active install path;
    *enabled* is always applied explicitly, never inherited.

    Args:
        name: Name of the installed extension to update.
        resolved_ref: The ``resolved_ref`` from a prior, validated check.
        enabled: Enabled state to apply to the new bundle.
        installed_dir: Defaults to ``~/.openhands/canvas-extensions/installed/``.

    Returns:
        None if not installed, else the InstallationInfo for the new bundle.

    Raises:
        ValueError: If *name* is invalid, *resolved_ref* could escape the
            staging directory, or no validated staged candidate matches it
            -- call check first.
    """
    installed_dir = _resolve_installed_dir(installed_dir)
    manager = _manager(installed_dir)
    current_info = manager.get(name)
    if current_info is None:
        return None

    staged_path = _staged_path(installed_dir, name, resolved_ref)
    if not staged_path.is_dir():
        raise ValueError(
            f"No validated staged update found for {name!r} at ref "
            f"{resolved_ref!r}. Call check_canvas_extension_update() first."
        )
    manifest = _load_validated_manifest(staged_path, name)

    install_path = installed_dir / name
    backup_path = _staging_root(installed_dir) / f"{name}.previous"
    shutil.rmtree(backup_path, ignore_errors=True)

    # POSIX rename() can't atomically replace a non-empty directory, so the
    # active bundle moves aside first; roll back if the second rename fails.
    os.replace(install_path, backup_path)
    try:
        os.replace(staged_path, install_path)
    except OSError:
        os.replace(backup_path, install_path)
        raise
    shutil.rmtree(backup_path, ignore_errors=True)
    shutil.rmtree(_staging_root(installed_dir) / name, ignore_errors=True)

    info = InstallationInfo.from_extension(
        manifest,
        source=current_info.source,
        install_path=install_path,
        requested_ref=current_info.requested_ref,
        resolved_ref=resolved_ref,
        repo_path=current_info.repo_path,
    )
    info.enabled = enabled

    with manager.metadata_session as session:
        session.extensions[name] = info

    return info
