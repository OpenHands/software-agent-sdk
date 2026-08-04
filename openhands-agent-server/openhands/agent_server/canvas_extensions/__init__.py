"""Canvas Extensions: installable UI bundles that contribute pages to Canvas."""

from openhands.agent_server.canvas_extensions.installed import (
    InstalledCanvasExtensionInfo,
    disable_canvas_extension,
    enable_canvas_extension,
    get_installed_canvas_extension,
    get_installed_canvas_extensions_dir,
    install_canvas_extension,
    list_installed_canvas_extensions,
    load_installed_canvas_extensions,
    uninstall_canvas_extension,
)
from openhands.agent_server.canvas_extensions.manifest import (
    MANIFEST_FILENAME,
    CanvasExtensionContributes,
    CanvasExtensionManifest,
    CanvasExtensionPage,
    resolve_entrypoint,
)


__all__ = [
    "CanvasExtensionManifest",
    "CanvasExtensionContributes",
    "CanvasExtensionPage",
    "MANIFEST_FILENAME",
    "resolve_entrypoint",
    "InstalledCanvasExtensionInfo",
    "install_canvas_extension",
    "uninstall_canvas_extension",
    "enable_canvas_extension",
    "disable_canvas_extension",
    "list_installed_canvas_extensions",
    "load_installed_canvas_extensions",
    "get_installed_canvas_extension",
    "get_installed_canvas_extensions_dir",
]
