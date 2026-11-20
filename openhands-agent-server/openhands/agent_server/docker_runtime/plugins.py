"""Copy server-installed plugins into a stopped runtime's private store."""

import shutil
import tempfile
from pathlib import Path

from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.sdk.extensions.installation import InstallationInfo
from openhands.sdk.extensions.installation.metadata import InstallationMetadata
from openhands.sdk.extensions.installation.utils import validate_extension_name
from openhands.sdk.logger import get_logger
from openhands.sdk.plugin.installed import PluginInstallationInterface
from openhands.sdk.utils.files import atomic_write_text


logger = get_logger(__name__)
_SERVER_SOURCE = "docker-runtime:server-installed"


def refresh_installed_plugins(
    source: Path, persistence_dir: Path, container_home: str
) -> None:
    """Refresh only server-owned copies, preserving container-local installations.

    Called before starting the container, so running conversations retain their
    snapshot. Copies remain installed/ambient, rather than explicit attachments.
    """
    try:
        _refresh_installed_plugins(source, persistence_dir, container_home)
    except Exception as exc:
        logger.warning(
            "Failed to refresh server-installed plugins for Docker runtime (%s)",
            type(exc).__name__,
        )


def _refresh_installed_plugins(
    source: Path, persistence_dir: Path, container_home: str
) -> None:
    host_metadata = InstallationMetadata.load_from_dir(source, strict=True)
    host_metadata.validate_tracked(source)
    if source.is_dir():
        host_metadata.discover_untracked(
            source, PluginInstallationInterface(), strict=True
        )
    installations = list(host_metadata.extensions.values())
    direct_child = RuntimeProvisioningStore.direct_child
    plugins_dir = direct_child(persistence_dir, "plugins")
    installed_dir = direct_child(plugins_dir, "installed")
    if not installations and not installed_dir.exists():
        return
    installed_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = direct_child(installed_dir, InstallationMetadata.metadata_filename)
    metadata = InstallationMetadata.load_from_dir(installed_dir, strict=True)
    managed = {
        name
        for name, info in metadata.extensions.items()
        if info.source == _SERVER_SOURCE
    }
    enabled = {info.name: info for info in installations if info.enabled}
    for name in managed - enabled.keys():
        validate_extension_name(name)
        destination = direct_child(installed_dir, name)
        if destination.exists():
            shutil.rmtree(destination)
        del metadata.extensions[name]

    for name, info in enabled.items():
        validate_extension_name(name)
        destination = direct_child(installed_dir, name)
        if destination.exists() and name not in managed:
            continue
        try:
            source_dir = direct_child(source, name)
            with tempfile.TemporaryDirectory(dir=persistence_dir.parent) as temporary:
                staged = Path(temporary) / name
                shutil.copytree(
                    source_dir,
                    staged,
                    symlinks=True,
                    ignore=shutil.ignore_patterns(".git"),
                )
                if destination.exists():
                    shutil.rmtree(destination)
                staged.rename(destination)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Failed to copy installed plugin '%s' for Docker runtime (%s)",
                name,
                type(exc).__name__,
            )
            continue
        # Fetch credentials and host paths are not needed to load a payload.
        metadata.extensions[name] = InstallationInfo(
            name=name,
            version=info.version,
            description=info.description,
            source=_SERVER_SOURCE,
            install_path=Path(container_home) / "plugins" / "installed" / name,
            installed_at=info.installed_at,
        )
    atomic_write_text(metadata_path, metadata.model_dump_json(indent=2))
