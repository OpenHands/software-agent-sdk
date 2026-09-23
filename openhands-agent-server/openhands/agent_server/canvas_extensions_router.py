"""Canvas Extensions router for OpenHands Agent Server.

HTTP API endpoints for canvas extension operations. Business logic is
delegated to ``canvas_extensions/installed.py``; this module mirrors
``plugins_router.py`` / ``skills_router.py`` and stays focused on HTTP
concerns: install / list / get / enable-disable / uninstall, plus serving
an installed extension's entrypoint bundle to the Canvas frontend.
"""

import asyncio
from typing import Annotated, Final

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from openhands.agent_server.canvas_extensions.backend import (
    BackendLogs,
    BackendRevisionRequest,
    BackendStatus,
    CanvasExtensionBackendManager,
)
from openhands.agent_server.canvas_extensions.bridge import AppBackendSessionStore
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
from openhands.agent_server.canvas_extensions.manifest import (
    MANIFEST_FILENAME,
    CanvasExtensionManifest,
)
from openhands.sdk.extensions.fetch import ExtensionFetchError
from openhands.sdk.utils.redact import redact_url_credentials


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


def _backend_manager(request: Request) -> CanvasExtensionBackendManager:
    manager = getattr(request.app.state, "canvas_extension_backend_manager", None)
    if manager is None:
        manager = CanvasExtensionBackendManager()
        request.app.state.canvas_extension_backend_manager = manager
    return manager


async def _revoke_and_stop_backend(
    request: Request, extension_name: str
) -> BackendStatus:
    session_store = getattr(request.app.state, "app_backend_session_store", None)
    if isinstance(session_store, AppBackendSessionStore):
        await session_store.revoke_app(extension_name)
    return await _backend_manager(request).stop(extension_name)


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
        400: {"description": "Could not read canvas extension source"},
        409: {"description": "Canvas extension already installed (use force=true)"},
        422: {"description": "Invalid canvas extension (bad manifest, etc.)"},
    },
)
async def install_canvas_extension_endpoint(
    request: InstallCanvasExtensionRequest,
    http_request: Request,
) -> InstalledCanvasExtensionResponse:
    """Install a canvas extension from a git URL, GitHub shorthand, or local
    path.

    A fresh install always lands disabled, regardless of the request body --
    there is no ``enabled`` field to smuggle a different state through.
    """
    manager = _backend_manager(http_request)
    if request.force and manager.has_running_backends():
        raise HTTPException(
            status_code=409,
            detail="Stop running Canvas App backends before a forced install",
        )
    try:
        info = await asyncio.to_thread(
            install_canvas_extension,
            source=request.source,
            ref=request.ref,
            repo_path=request.repo_path,
            force=request.force,
        )
        manager.invalidate_approval(info.name)
        return InstalledCanvasExtensionResponse.from_info(
            info, get_installed_canvas_extension_manifest(info.name)
        )
    except FileExistsError:
        raise HTTPException(
            status_code=409,
            detail="Canvas extension already installed. Use force=true to overwrite.",
        )
    except ExtensionFetchError as e:
        # Pass the specific reason through -- which of source/ref/repo_path is
        # wrong is the whole diagnostic, and a generic message misattributes a
        # bad repo_path to the source. Redacted in case a URL carried a token.
        # Deliberately not worded "failed to fetch": the canvas client treats
        # that phrase as a network outage and hides the reason behind a
        # "Disconnected, check your network" toast.
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not read canvas extension source: "
                f"{redact_url_credentials(str(e))}"
            ),
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No {MANIFEST_FILENAME} found at the resolved location. "
                "Source plus Path must point at the extension's own "
                "directory, not the repository root."
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
async def set_canvas_extension_enabled_endpoint(
    extension_name: CanvasExtensionNamePath,
    request: UpdateCanvasExtensionStateRequest,
    http_request: Request,
) -> UpdateCanvasExtensionStateResponse:
    """Enable or disable an installed canvas extension."""
    if not request.enabled:
        await _backend_manager(http_request).stop(extension_name)
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
async def uninstall_canvas_extension_endpoint(
    extension_name: CanvasExtensionNamePath,
    request: Request,
) -> UninstallCanvasExtensionResponse:
    """Uninstall a canvas extension by name while retaining backend data."""
    await _backend_manager(request).stop(extension_name)
    if not uninstall_canvas_extension(name=extension_name):
        raise HTTPException(
            status_code=404,
            detail=f"Canvas extension '{extension_name}' is not installed",
        )
    return UninstallCanvasExtensionResponse(
        message=f"Canvas extension '{extension_name}' uninstalled"
    )


@canvas_extensions_router.get(
    "/installed/{extension_name}/backend",
    response_model=BackendStatus,
)
async def get_canvas_extension_backend_status_endpoint(
    extension_name: CanvasExtensionNamePath,
    request: Request,
) -> BackendStatus:
    """Probe the optional backend lifecycle state."""
    return await _backend_manager(request).status(extension_name)


@canvas_extensions_router.post(
    "/installed/{extension_name}/backend/prepare",
    response_model=BackendStatus,
    responses={409: {"description": "Revision or checksum approval conflict"}},
)
async def prepare_canvas_extension_backend_endpoint(
    extension_name: CanvasExtensionNamePath,
    body: BackendRevisionRequest,
    request: Request,
) -> BackendStatus:
    """Verify and prepare the immutable artifact for an exact revision."""
    try:
        return await _backend_manager(request).prepare(extension_name, body.revision)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@canvas_extensions_router.post(
    "/installed/{extension_name}/backend/start",
    response_model=BackendStatus,
    responses={409: {"description": "Revision is not prepared or cannot start"}},
)
async def start_canvas_extension_backend_endpoint(
    extension_name: CanvasExtensionNamePath,
    body: BackendRevisionRequest,
    request: Request,
) -> BackendStatus:
    """Start only a previously prepared exact backend revision."""
    try:
        return await _backend_manager(request).start(extension_name, body.revision)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@canvas_extensions_router.post(
    "/installed/{extension_name}/backend/stop",
    response_model=BackendStatus,
)
async def stop_canvas_extension_backend_endpoint(
    extension_name: CanvasExtensionNamePath,
    request: Request,
) -> BackendStatus:
    """Stop the backend's owned process group idempotently."""
    return await _backend_manager(request).stop(extension_name)


@canvas_extensions_router.get(
    "/installed/{extension_name}/backend/logs",
    response_model=BackendLogs,
)
async def get_canvas_extension_backend_logs_endpoint(
    extension_name: CanvasExtensionNamePath,
    request: Request,
    limit_bytes: Annotated[int, Query(ge=1, le=256 * 1024)] = 64 * 1024,
) -> BackendLogs:
    """Return bounded combined stdout and stderr from the owned process."""
    return await _backend_manager(request).logs(extension_name, limit_bytes)


@canvas_extensions_router.delete(
    "/installed/{extension_name}/backend/data",
    response_model=BackendStatus,
    responses={409: {"description": "Backend must be stopped first"}},
)
async def delete_canvas_extension_backend_data_endpoint(
    extension_name: CanvasExtensionNamePath,
    request: Request,
) -> BackendStatus:
    """Delete mutable backend data separately from app uninstall."""
    try:
        return await _backend_manager(request).delete_data(extension_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
