"""Plugins router for OpenHands Agent Server.

HTTP API endpoints for plugin operations. Business logic is delegated to
``plugins_service.py``; this module mirrors ``skills_router.py`` and stays
focused on HTTP concerns. It exposes:

* Installed-plugin management — install / list / enable-disable / uninstall /
  refresh — plus listing locally-available plugins.
* The plugins-only marketplace catalog.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from openhands.agent_server.plugins_service import (
    MarketplacePluginInfo,
    PluginSkillSummary,
    service_disable_plugin,
    service_enable_plugin,
    service_get_installed_plugin,
    service_get_plugins_marketplace_catalog,
    service_install_plugin,
    service_list_available_plugins,
    service_list_installed_plugins,
    service_load_plugin_contents,
    service_plugin_contents,
    service_uninstall_plugin,
    service_update_plugin,
)
from openhands.sdk.extensions.fetch import ExtensionFetchError
from openhands.sdk.plugin import InstalledPluginInfo, Plugin, PluginFetchError


plugins_router = APIRouter(prefix="/plugins", tags=["Plugins"])

# Kebab-case plugin name — matches the SDK's installed-plugin name rule. Guards
# the {plugin_name} path parameter against empty strings, path traversal, and
# invalid characters.
PLUGIN_NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

PluginNamePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=255,
        pattern=PLUGIN_NAME_PATTERN,
        description="Plugin name (lowercase alphanumeric, hyphens)",
    ),
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PluginsRequest(BaseModel):
    """Request body for listing locally-available plugins."""

    load_user: bool = Field(
        default=True, description="Load user plugins (~/.agents/plugins, etc.)"
    )
    load_project: bool = Field(
        default=True, description="Load project plugins from the workspace"
    )
    project_dir: str | None = Field(
        default=None, description="Workspace directory path for project plugins"
    )


class PluginInfo(BaseModel):
    """Summary of an available plugin."""

    name: str
    version: str = ""
    description: str = ""
    path: str = Field(default="", description="Path to the plugin directory")
    skills: list[PluginSkillSummary] = Field(
        default_factory=list, description="Skills bundled in the plugin"
    )
    files: list[str] = Field(
        default_factory=list,
        description="Plugin files, relative to the plugin directory",
    )


class PluginsResponse(BaseModel):
    """Response containing the locally-available plugins."""

    plugins: list[PluginInfo]


class InstallPluginRequest(BaseModel):
    """Request body for installing a plugin."""

    source: str = Field(
        min_length=1,
        description=(
            "Plugin source - git URL, GitHub shorthand, or local path. Examples: "
            "'github:OpenHands/extensions/plugins/city-weather', '/path/to/plugin'"
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


class InstalledPluginResponse(BaseModel):
    """Response containing installed plugin information."""

    name: str = Field(description="Plugin name")
    version: str = Field(default="", description="Plugin version")
    description: str = Field(default="", description="Plugin description")
    enabled: bool = Field(default=True, description="Whether the plugin is enabled")
    source: str = Field(description="Original source (e.g., 'github:owner/repo')")
    resolved_ref: str | None = Field(
        default=None, description="Resolved git commit SHA"
    )
    repo_path: str | None = Field(
        default=None, description="Subdirectory path within the repository"
    )
    installed_at: str = Field(description="ISO 8601 timestamp of installation")
    install_path: str = Field(description="Path where the plugin is installed")
    skills: list[PluginSkillSummary] | None = Field(
        default=None,
        description="Skills bundled in the plugin (None if it failed to load)",
    )
    files: list[str] | None = Field(
        default=None,
        description="Plugin files, relative to install_path (None if load failed)",
    )

    @classmethod
    def from_plugin_info(cls, info: InstalledPluginInfo) -> "InstalledPluginResponse":
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
        )


class InstalledPluginsListResponse(BaseModel):
    """Response containing the list of installed plugins."""

    plugins: list[InstalledPluginResponse]


class UpdatePluginStateRequest(BaseModel):
    """Request body for updating plugin state (enable/disable)."""

    enabled: bool


class UpdatePluginStateResponse(BaseModel):
    """Response from a plugin state update."""

    name: str
    enabled: bool


class UninstallPluginResponse(BaseModel):
    """Response from a plugin uninstall."""

    message: str


class UpdatePluginResponse(BaseModel):
    """Response from a plugin refresh/update."""

    message: str
    plugin: InstalledPluginResponse


class MarketplaceCatalogResponse(BaseModel):
    """Response containing the plugins marketplace catalog."""

    plugins: list[MarketplacePluginInfo]


def _installed_response(info: InstalledPluginInfo) -> InstalledPluginResponse:
    """Build an installed-plugin response enriched with the plugin's contents."""
    response = InstalledPluginResponse.from_plugin_info(info)
    contents = service_load_plugin_contents(info.install_path)
    if contents is not None:
        response.skills, response.files = contents
    return response


def _plugin_info(plugin: Plugin) -> PluginInfo:
    """Build an available-plugin summary including its contents."""
    skills, files = service_plugin_contents(plugin)
    return PluginInfo(
        name=plugin.name,
        version=plugin.version,
        description=plugin.description,
        path=plugin.path,
        skills=skills,
        files=files,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@plugins_router.post("", response_model=PluginsResponse)
def get_plugins(request: PluginsRequest) -> PluginsResponse:
    """List locally-available plugins (enabled installed + user/project dirs).

    Args:
        request: Which local sources to load.

    Returns:
        PluginsResponse with available plugin summaries.
    """
    plugins = service_list_available_plugins(
        load_user=request.load_user,
        load_project=request.load_project,
        project_dir=request.project_dir,
    )
    return PluginsResponse(plugins=[_plugin_info(p) for p in plugins])


@plugins_router.post(
    "/install",
    response_model=InstalledPluginResponse,
    responses={
        400: {"description": "Failed to fetch plugin source"},
        409: {"description": "Plugin already installed (use force=true)"},
        422: {"description": "Invalid plugin (bad name, etc.)"},
    },
)
def install_plugin_endpoint(request: InstallPluginRequest) -> InstalledPluginResponse:
    """Install a plugin from a git URL, GitHub shorthand, or local path."""
    try:
        info = service_install_plugin(
            source=request.source,
            ref=request.ref,
            repo_path=request.repo_path,
            force=request.force,
        )
        return _installed_response(info)
    except FileExistsError:
        raise HTTPException(
            status_code=409,
            detail="Plugin already installed. Use force=true to overwrite.",
        )
    except (PluginFetchError, ExtensionFetchError):
        raise HTTPException(
            status_code=400,
            detail="Failed to fetch plugin source. Check that the source is valid.",
        )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid plugin. Ensure it has a valid kebab-case name.",
        )


@plugins_router.get("/installed", response_model=InstalledPluginsListResponse)
def list_installed_plugins_endpoint() -> InstalledPluginsListResponse:
    """List all installed plugins (enabled and disabled)."""
    plugins = service_list_installed_plugins()
    return InstalledPluginsListResponse(
        plugins=[_installed_response(info) for info in plugins]
    )


@plugins_router.get(
    "/installed/{plugin_name}",
    response_model=InstalledPluginResponse,
    responses={404: {"description": "Plugin not installed"}},
)
def get_installed_plugin_endpoint(
    plugin_name: PluginNamePath,
) -> InstalledPluginResponse:
    """Get information about a specific installed plugin."""
    info = service_get_installed_plugin(name=plugin_name)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_name}' is not installed",
        )
    return _installed_response(info)


@plugins_router.patch(
    "/installed/{plugin_name}",
    response_model=UpdatePluginStateResponse,
    responses={404: {"description": "Plugin not installed"}},
)
def set_plugin_enabled_endpoint(
    plugin_name: PluginNamePath, request: UpdatePluginStateRequest
) -> UpdatePluginStateResponse:
    """Enable or disable an installed plugin."""
    fn = service_enable_plugin if request.enabled else service_disable_plugin
    if not fn(name=plugin_name):
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_name}' is not installed",
        )
    return UpdatePluginStateResponse(name=plugin_name, enabled=request.enabled)


@plugins_router.delete(
    "/installed/{plugin_name}",
    response_model=UninstallPluginResponse,
    responses={404: {"description": "Plugin not installed"}},
)
def uninstall_plugin_endpoint(plugin_name: PluginNamePath) -> UninstallPluginResponse:
    """Uninstall a plugin by name."""
    if not service_uninstall_plugin(name=plugin_name):
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_name}' is not installed",
        )
    return UninstallPluginResponse(message=f"Plugin '{plugin_name}' uninstalled")


@plugins_router.post(
    "/installed/{plugin_name}/refresh",
    response_model=UpdatePluginResponse,
    responses={404: {"description": "Plugin not installed"}},
)
def refresh_plugin_endpoint(plugin_name: PluginNamePath) -> UpdatePluginResponse:
    """Refresh an installed plugin to the latest version from its source."""
    info = service_update_plugin(name=plugin_name)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_name}' is not installed",
        )
    return UpdatePluginResponse(
        message=f"Plugin '{plugin_name}' updated",
        plugin=_installed_response(info),
    )


@plugins_router.get("/marketplace", response_model=MarketplaceCatalogResponse)
def get_marketplace_catalog() -> MarketplaceCatalogResponse:
    """Get the plugins marketplace catalog with installation status.

    Returns the true plugins (entries whose source lives under ``./plugins/``)
    from the OpenHands extensions repository marketplace, each with attachable
    ``PluginSource`` coordinates (``source`` / ``ref`` / ``repo_path``) and an
    ``installed`` flag. This enables the front-end to render a plugins
    marketplace with install/installed state and to attach plugins to
    conversations.

    Returns:
        MarketplaceCatalogResponse containing the list of available plugins.
    """
    return MarketplaceCatalogResponse(plugins=service_get_plugins_marketplace_catalog())
