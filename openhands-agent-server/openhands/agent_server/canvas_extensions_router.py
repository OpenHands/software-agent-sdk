"""Canvas Extensions router for OpenHands Agent Server.

HTTP API endpoints for canvas extension operations. Business logic is
delegated to ``canvas_extensions/installed.py``; this module mirrors
``plugins_router.py`` / ``skills_router.py`` and stays focused on HTTP
concerns: install / list / get / enable-disable / uninstall, plus serving
an installed extension's entrypoint bundle to the Canvas frontend.
"""

from typing import Annotated, Final

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from openhands.agent_server.canvas_extensions.installed import (
    InstalledCanvasExtensionInfo,
    disable_canvas_extension,
    enable_canvas_extension,
    get_canvas_extension_bundle_path,
    get_installed_canvas_extension,
    get_installed_canvas_extension_manifest,
    install_canvas_extension,
    list_installed_canvas_extensions,
    uninstall_canvas_extension,
)
from openhands.agent_server.canvas_extensions.manifest import CanvasExtensionManifest
from openhands.sdk.extensions.fetch import ExtensionFetchError


canvas_extensions_router = APIRouter(
    prefix="/canvas-extensions", tags=["Canvas Extensions"]
)

# Matches the SDK's validate_extension_name() rule.
CANVAS_EXTENSION_NAME_PATTERN: Final[str] = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

CanvasExtensionNamePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=255,
        pattern=CANVAS_EXTENSION_NAME_PATTERN,
        description="Canvas extension name (lowercase alphanumeric, hyphens)",
    ),
]


class InstallCanvasExtensionRequest(BaseModel):
    """Request body for installing a canvas extension."""

    source: str = Field(
        min_length=1,
        description=(
            "Canvas extension source - git URL, GitHub shorthand, or local "
            "path. Examples: 'github:owner/repo', '/path/to/extension'"
        ),
    )
    ref: str | None = Field(
        default=None, description="Optional branch, tag, or commit to install"
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within the repository (for monorepos)",
    )
    force: bool = Field(
        default=False, description="If true, overwrite existing installation"
    )


class InstalledCanvasExtensionResponse(BaseModel):
    """Response containing installed canvas extension information."""

    name: str = Field(description="Canvas extension name")
    version: str = Field(default="", description="Canvas extension version")
    description: str = Field(default="", description="Canvas extension description")
    enabled: bool = Field(
        default=False, description="Whether the canvas extension is enabled"
    )
    source: str = Field(description="Original source (e.g., 'github:owner/repo')")
    resolved_ref: str | None = Field(
        default=None, description="Resolved git commit SHA"
    )
    repo_path: str | None = Field(
        default=None, description="Subdirectory path within the repository"
    )
    installed_at: str = Field(description="ISO 8601 timestamp of installation")
    install_path: str = Field(description="Path where the extension is installed")
    manifest: CanvasExtensionManifest | None = Field(
        default=None,
        description=(
            "The extension's validated manifest (display_name, "
            "contributes.pages, ...). None if the installed manifest can "
            "no longer be read."
        ),
    )

    @classmethod
    def from_info(
        cls,
        info: InstalledCanvasExtensionInfo,
        manifest: CanvasExtensionManifest | None,
    ) -> "InstalledCanvasExtensionResponse":
        return cls(
            name=info.name,
            version=info.version,
            description=info.description,
            enabled=info.enabled,
            source=info.source,
            resolved_ref=info.resolved_ref,
            repo_path=info.repo_path,
            installed_at=info.installed_at,
            install_path=str(info.install_path),
            manifest=manifest,
        )


class InstalledCanvasExtensionsListResponse(BaseModel):
    """Response containing the list of installed canvas extensions."""

    canvas_extensions: list[InstalledCanvasExtensionResponse]


class UpdateCanvasExtensionStateRequest(BaseModel):
    """Request body for updating canvas extension state (enable/disable)."""

    enabled: bool


class UpdateCanvasExtensionStateResponse(BaseModel):
    """Response from a canvas extension state update."""

    name: str
    enabled: bool


class UninstallCanvasExtensionResponse(BaseModel):
    """Response from a canvas extension uninstall."""

    message: str


@canvas_extensions_router.post(
    "/install",
    response_model=InstalledCanvasExtensionResponse,
    responses={
        400: {"description": "Failed to fetch canvas extension source"},
        409: {"description": "Canvas extension already installed (use force=true)"},
        422: {"description": "Invalid canvas extension (bad manifest, etc.)"},
    },
)
def install_canvas_extension_endpoint(
    request: InstallCanvasExtensionRequest,
) -> InstalledCanvasExtensionResponse:
    """Install a canvas extension from a git URL, GitHub shorthand, or local
    path.

    A fresh install always lands disabled, regardless of the request body --
    there is no ``enabled`` field to smuggle a different state through.
    """
    try:
        info = install_canvas_extension(
            source=request.source,
            ref=request.ref,
            repo_path=request.repo_path,
            force=request.force,
        )
        return InstalledCanvasExtensionResponse.from_info(
            info, get_installed_canvas_extension_manifest(info.name)
        )
    except FileExistsError:
        raise HTTPException(
            status_code=409,
            detail="Canvas extension already installed. Use force=true to overwrite.",
        )
    except ExtensionFetchError:
        raise HTTPException(
            status_code=400,
            detail=(
                "Failed to fetch canvas extension source. "
                "Check that the source is valid."
            ),
        )
    except (ValidationError, ValueError, OSError):
        raise HTTPException(
            status_code=422,
            detail="Invalid canvas extension. Ensure the manifest is well-formed.",
        )


@canvas_extensions_router.get(
    "/installed", response_model=InstalledCanvasExtensionsListResponse
)
def list_installed_canvas_extensions_endpoint() -> (
    InstalledCanvasExtensionsListResponse
):
    """List all installed canvas extensions (enabled and disabled)."""
    infos = list_installed_canvas_extensions()
    return InstalledCanvasExtensionsListResponse(
        canvas_extensions=[
            InstalledCanvasExtensionResponse.from_info(
                i, get_installed_canvas_extension_manifest(i.name)
            )
            for i in infos
        ]
    )


@canvas_extensions_router.get(
    "/installed/{extension_name}",
    response_model=InstalledCanvasExtensionResponse,
    responses={404: {"description": "Canvas extension not installed"}},
)
def get_installed_canvas_extension_endpoint(
    extension_name: CanvasExtensionNamePath,
) -> InstalledCanvasExtensionResponse:
    """Get information about a specific installed canvas extension."""
    info = get_installed_canvas_extension(name=extension_name)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Canvas extension '{extension_name}' is not installed",
        )
    return InstalledCanvasExtensionResponse.from_info(
        info, get_installed_canvas_extension_manifest(extension_name)
    )


@canvas_extensions_router.patch(
    "/installed/{extension_name}",
    response_model=UpdateCanvasExtensionStateResponse,
    responses={404: {"description": "Canvas extension not installed"}},
)
def set_canvas_extension_enabled_endpoint(
    extension_name: CanvasExtensionNamePath, request: UpdateCanvasExtensionStateRequest
) -> UpdateCanvasExtensionStateResponse:
    """Enable or disable an installed canvas extension."""
    fn = enable_canvas_extension if request.enabled else disable_canvas_extension
    if not fn(name=extension_name):
        raise HTTPException(
            status_code=404,
            detail=f"Canvas extension '{extension_name}' is not installed",
        )
    return UpdateCanvasExtensionStateResponse(
        name=extension_name, enabled=request.enabled
    )


@canvas_extensions_router.delete(
    "/installed/{extension_name}",
    response_model=UninstallCanvasExtensionResponse,
    responses={404: {"description": "Canvas extension not installed"}},
)
def uninstall_canvas_extension_endpoint(
    extension_name: CanvasExtensionNamePath,
) -> UninstallCanvasExtensionResponse:
    """Uninstall a canvas extension by name."""
    if not uninstall_canvas_extension(name=extension_name):
        raise HTTPException(
            status_code=404,
            detail=f"Canvas extension '{extension_name}' is not installed",
        )
    return UninstallCanvasExtensionResponse(
        message=f"Canvas extension '{extension_name}' uninstalled"
    )


@canvas_extensions_router.get(
    "/installed/{extension_name}/bundle",
    responses={404: {"description": "Canvas extension or bundle not found"}},
)
def get_canvas_extension_bundle_endpoint(
    extension_name: CanvasExtensionNamePath,
) -> FileResponse:
    """Serve an installed canvas extension's entrypoint bundle file.

    Entrypoint containment is re-validated against the live install path on
    every request, not just at install time -- see
    ``get_canvas_extension_bundle_path``. ``Cache-Control: no-cache`` forces
    revalidation on each request, so a client never keeps serving a bundle
    from before the extension's last refresh: the actual bytes are re-read
    from disk, and Starlette's file-stat-based ``ETag``/``Last-Modified``
    change the moment the on-disk revision does.
    """
    bundle_path = get_canvas_extension_bundle_path(name=extension_name)
    if bundle_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Canvas extension '{extension_name}' bundle not found",
        )
    return FileResponse(bundle_path, headers={"Cache-Control": "no-cache"})
