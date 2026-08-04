"""Installed Canvas Extensions: the disabled-by-default guarantee.

Built on ``openhands.sdk.extensions.installation``, the shared install-
tracking framework Plugins/Skills also use. Not reused unmodified though:
``InstallationInfo.enabled`` defaults to ``True``, and neither
``InstallationManager.install()`` nor its self-healing directory discovery
override that for a genuinely new entry (only a force-reinstall of an
already-tracked name preserves its prior state). This module corrects that,
as an explicit post-write step, so any newly created entry — from an
install or from discovery — lands disabled until explicitly enabled.
"""

from pathlib import Path

from openhands.agent_server.canvas_extensions.manifest import (
    MANIFEST_FILENAME,
    CanvasExtensionManifest,
    resolve_entrypoint,
)
from openhands.sdk.extensions.installation import (
    InstallationInfo,
    InstallationInterface,
    InstallationManager,
    InstallationMetadata,
)


# Public type alias, matching the InstalledPluginInfo convention.
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
        # Runs on every load (install + discovery): a parseable manifest
        # isn't enough to trust the directory, containment must hold too.
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
    """Names backed by a real, valid tracked entry -- not just a metadata key.

    A stale record with no matching directory (e.g. seeded to smuggle
    ``enabled: true`` ahead of an install it doesn't correspond to yet)
    must not count as "already installed", or a real install of that name
    would inherit it as if it were a legitimate force-reinstall.
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

    ``pre_existing`` (names tracked *before* the write that produced
    ``info``) distinguishes a genuinely new entry from a force-reinstall of
    an already-tracked one, whose prior enabled state is already preserved
    correctly -- see the module docstring.
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

    A newly created tracked entry always lands disabled — enabling is a
    separate, explicit step, regardless of anything a caller passes in.
    See the module docstring for why this doesn't just delegate to
    ``InstallationManager.install()`` unmodified.

    Args:
        source: Extension source — ``"github:owner/repo"``, git URL, or
            local path.
        ref: Optional branch, tag, or commit to install.
        repo_path: Subdirectory path within the repository (for monorepos).
        installed_dir: Directory for installed canvas extensions. Defaults
            to ``~/.openhands/canvas-extensions/installed/``.
        force: If True, overwrite an existing installation.

    Returns:
        InstalledCanvasExtensionInfo with details about the installation.
    """
    installed_dir = _resolve_installed_dir(installed_dir)
    manager = _manager(installed_dir)
    pre_existing = _tracked_names(installed_dir)
    info = manager.install(source, ref=ref, repo_path=repo_path, force=force)
    return _force_disable_new(manager, info, pre_existing)


def uninstall_canvas_extension(name: str, installed_dir: Path | None = None) -> bool:
    """Uninstall a canvas extension by name.

    Returns:
        True if the extension was uninstalled, False if it wasn't installed.
    """
    return _manager(_resolve_installed_dir(installed_dir)).uninstall(name)


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

    Self-healing like ``InstallationManager.list_installed()``. A directory
    discovered this way (dropped in directly, bypassing
    ``install_canvas_extension``) also lands disabled — see the module
    docstring.
    """
    installed_dir = _resolve_installed_dir(installed_dir)
    manager = _manager(installed_dir)
    pre_existing = _tracked_names(installed_dir)
    infos = manager.list_installed()
    return [_force_disable_new(manager, info, pre_existing) for info in infos]


def load_installed_canvas_extensions(
    installed_dir: Path | None = None,
) -> list[CanvasExtensionManifest]:
    """Load all enabled canvas extensions' manifests."""
    return _manager(_resolve_installed_dir(installed_dir)).load_installed()


def get_installed_canvas_extension(
    name: str, installed_dir: Path | None = None
) -> InstalledCanvasExtensionInfo | None:
    """Get information about a specific installed canvas extension."""
    return _manager(_resolve_installed_dir(installed_dir)).get(name)
