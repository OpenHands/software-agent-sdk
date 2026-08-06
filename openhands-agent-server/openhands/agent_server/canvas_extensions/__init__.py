"""Canvas Extensions: installable UI bundles that contribute pages to Canvas."""

from openhands.agent_server.canvas_extensions.manifest import (
    CanvasExtensionContributes,
    CanvasExtensionManifest,
    CanvasExtensionPage,
    resolve_entrypoint,
)


__all__ = [
    "CanvasExtensionManifest",
    "CanvasExtensionContributes",
    "CanvasExtensionPage",
    "resolve_entrypoint",
]
