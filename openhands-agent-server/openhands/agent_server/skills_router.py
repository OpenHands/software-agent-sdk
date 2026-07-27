"""Skills router for OpenHands Agent Server.

This module defines the HTTP API endpoints for skill operations.
Business logic is delegated to skills_service.py.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field

from openhands.agent_server.skills_service import (
    ExposedUrlData,
    MarketplaceSkillInfo,
    load_all_skills,
    service_disable_skill,
    service_enable_skill,
    service_get_installed_skill,
    service_get_marketplace_catalog,
    service_install_skill,
    service_list_installed_skills,
    service_uninstall_skill,
    service_update_skill,
    sync_public_skills,
)
from openhands.sdk.extensions.fetch import ExtensionFetchError
from openhands.sdk.marketplace.registration import MarketplaceRegistration
from openhands.sdk.skills import (
    InstalledSkillInfo,
    SkillFetchError,
    SkillValidationError,
)
from openhands.sdk.skills.skill import DEFAULT_MARKETPLACE_PATH
from openhands.sdk.skills.utils import SKILL_NAME_PATTERN


skills_router = APIRouter(prefix="/skills", tags=["Skills"])

# Validated skill name path parameter
# Prevents empty strings, path traversal, and invalid characters
SkillNamePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=255,
        pattern=SKILL_NAME_PATTERN.pattern,
        description="Skill name (lowercase alphanumeric, hyphens)",
    ),
]


class ExposedUrl(BaseModel):
    """Represents an exposed URL from the sandbox."""

    name: str
    url: str
    port: int


class OrgConfig(BaseModel):
    """Configuration for loading organization-level skills."""

    repository: str = Field(description="Selected repository (e.g., 'owner/repo')")
    provider: str = Field(
        description="Git provider type: github, gitlab, azure, bitbucket"
    )
    org_repo_url: str = Field(
        description="Pre-authenticated Git URL for the organization repository. "
        "Contains sensitive credentials - handle with care and avoid logging."
    )
    org_name: str = Field(description="Organization name")


class SandboxConfig(BaseModel):
    """Configuration for loading sandbox-specific skills."""

    exposed_urls: list[ExposedUrl] = Field(
        default_factory=list,
        description="List of exposed URLs from the sandbox",
    )


class SkillsRequest(BaseModel):
    """Request body for loading skills."""

    load_public: bool = Field(
        default=True, description="Load public skills from OpenHands/extensions repo"
    )
    load_user: bool = Field(
        default=True, description="Load user skills from ~/.openhands/skills/"
    )
    load_project: bool = Field(
        default=True, description="Load project skills from workspace"
    )
    load_org: bool = Field(default=True, description="Load organization-level skills")
    marketplace_path: str | None = Field(
        default=DEFAULT_MARKETPLACE_PATH,
        description=(
            "Relative marketplace JSON path for public skills. "
            "Set to null to load all public skills."
        ),
    )
    registered_marketplaces: list[MarketplaceRegistration] = Field(
        default_factory=list,
        description=(
            "Marketplace registrations for plugin-based skill loading. Registrations "
            "with auto_load=True or a list of plugin names add their skills to the "
            "public skills."
        ),
    )

    project_dir: str | None = Field(
        default=None, description="Workspace directory path for project skills"
    )
    org_configs: list[OrgConfig] | None = Field(
        default=None,
        description="Organization/user skill repositories to load concurrently",
    )
    org_config: OrgConfig | None = Field(
        default=None,
        deprecated=True,
        description=(
            "Deprecated since v1.28.0 and scheduled for removal in v1.33.0. "
            "Single organization skills configuration; prefer org_configs."
        ),
    )
    sandbox_config: SandboxConfig | None = Field(
        default=None, description="Sandbox skills configuration"
    )


class SkillInfo(BaseModel):
    """Skill information returned by the API."""

    name: str
    type: Literal["repo", "knowledge", "agentskills"]
    content: str
    triggers: list[str] = Field(default_factory=list)
    source: str | None = None
    description: str | None = None
    is_agentskills_format: bool = False
    disable_model_invocation: bool = False
    allowed_tools: list[str] | None = None


class SkillsResponse(BaseModel):
    """Response containing all available skills."""

    skills: list[SkillInfo]
    sources: dict[str, int] = Field(
        default_factory=dict,
        description="Count of skills loaded from each source",
    )


class SyncResponse(BaseModel):
    """Response from skill sync operation."""

    status: Literal["success", "error"]
    message: str


# ---------------------------------------------------------------------------
# Installed Skills Management Models
# ---------------------------------------------------------------------------


class InstallSkillRequest(BaseModel):
    """Request body for installing a skill."""

    source: str = Field(
        min_length=1,
        description=(
            "Skill source - git URL, GitHub shorthand, or local path. "
            "Examples: "
            "'https://github.com/OpenHands/extensions/tree/main/skills/github', "
            "'github:OpenHands/extensions/skills/github', "
            "'/path/to/skill'"
        ),
    )
    ref: str | None = Field(
        default=None,
        description="Optional branch, tag, or commit to install",
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within the repository (for monorepos)",
    )
    force: bool = Field(
        default=False,
        description="If true, overwrite existing installation",
    )


class InstalledSkillResponse(BaseModel):
    """Response containing installed skill information."""

    name: str = Field(description="Skill name")
    version: str = Field(default="", description="Skill version")
    description: str = Field(default="", description="Skill description")
    enabled: bool = Field(default=True, description="Whether the skill is enabled")
    source: str = Field(description="Original source (e.g., 'github:owner/repo')")
    resolved_ref: str | None = Field(
        default=None, description="Resolved git commit SHA"
    )
    repo_path: str | None = Field(
        default=None, description="Subdirectory path within the repository"
    )
    installed_at: str = Field(description="ISO 8601 timestamp of installation")
    install_path: str = Field(description="Path where the skill is installed")

    @classmethod
    def from_skill_info(cls, info: InstalledSkillInfo) -> "InstalledSkillResponse":
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


class InstalledSkillsListResponse(BaseModel):
    """Response containing list of installed skills."""

    skills: list[InstalledSkillResponse]


class UpdateSkillStateRequest(BaseModel):
    """Request body for updating skill state (enable/disable)."""

    enabled: bool


class UpdateSkillStateResponse(BaseModel):
    """Response from skill state update operation."""

    name: str
    enabled: bool


class UninstallSkillResponse(BaseModel):
    """Response from skill uninstall operation."""

    message: str


class UpdateSkillResponse(BaseModel):
    """Response from skill update operation."""

    message: str
    skill: InstalledSkillResponse


class MarketplaceCatalogResponse(BaseModel):
    """Response containing the marketplace catalog."""

    skills: list[MarketplaceSkillInfo]


def _merge_registered_marketplaces(
    server_registrations: list[MarketplaceRegistration],
    request_registrations: list[MarketplaceRegistration],
) -> list[MarketplaceRegistration]:
    registrations_by_name = {
        registration.name: registration for registration in server_registrations
    }
    registrations_by_name.update(
        {registration.name: registration for registration in request_registrations}
    )
    return list(registrations_by_name.values())


@skills_router.post("", response_model=SkillsResponse)
def get_skills(request: SkillsRequest, http_request: Request) -> SkillsResponse:
    """Load and merge skills from all configured sources.

    Skills are loaded from multiple sources and merged with the following
    precedence (later overrides earlier for duplicate names):
    1. Sandbox skills (lowest) - Exposed URLs from sandbox
    2. Public skills - From GitHub OpenHands/extensions repository
    3. User skills - From ~/.openhands/skills/
    4. Organization skills - From {org}/.openhands or equivalent
    5. Project skills (highest) - From {workspace}/.openhands/skills/

    Args:
        request: SkillsRequest containing configuration for which sources to load.

    Returns:
        SkillsResponse containing merged skills and source counts.
    """
    # Convert Pydantic models to service data types
    sandbox_urls = None
    if request.sandbox_config and request.sandbox_config.exposed_urls:
        sandbox_urls = [
            ExposedUrlData(name=url.name, url=url.url, port=url.port)
            for url in request.sandbox_config.exposed_urls
        ]

    # Prefer the list form; fall back to the deprecated single org_config so
    # older app-servers keep working.
    org_repos: list[tuple[str, str]] = []
    if request.org_configs:
        org_repos = [(c.org_repo_url, c.org_name) for c in request.org_configs]
    elif "org_config" in request.model_fields_set and request.org_config:
        org_repos = [(request.org_config.org_repo_url, request.org_config.org_name)]

    config = getattr(http_request.app.state, "config", None)
    server_registrations = config.registered_marketplaces if config is not None else []
    registered_marketplaces = _merge_registered_marketplaces(
        server_registrations,
        request.registered_marketplaces,
    )

    # Call the service
    result = load_all_skills(
        load_public=request.load_public,
        load_user=request.load_user,
        load_project=request.load_project,
        load_org=request.load_org,
        project_dir=request.project_dir,
        org_repos=org_repos,
        sandbox_exposed_urls=sandbox_urls,
        marketplace_path=request.marketplace_path,
        registered_marketplaces=registered_marketplaces,
    )

    # Convert Skill objects to SkillInfo for response
    skills_info = [
        SkillInfo(
            name=info.name,
            type=info.type,
            content=info.content,
            triggers=info.triggers,
            source=info.source,
            description=info.description,
            is_agentskills_format=info.is_agentskills_format,
            disable_model_invocation=info.disable_model_invocation,
            allowed_tools=info.allowed_tools,
        )
        for info in (skill.to_skill_info() for skill in result.skills)
    ]

    return SkillsResponse(skills=skills_info, sources=result.sources)


@skills_router.post("/sync", response_model=SyncResponse)
def sync_skills() -> SyncResponse:
    """Force refresh of public skills from GitHub repository.

    This triggers a git pull on the cached skills repository to get
    the latest skills from the OpenHands/extensions repository.

    Returns:
        SyncResponse indicating success or failure.
    """
    success, message = sync_public_skills()
    return SyncResponse(
        status="success" if success else "error",
        message=message,
    )


# ---------------------------------------------------------------------------
# Installed Skills Management Endpoints
# ---------------------------------------------------------------------------


@skills_router.post(
    "/install",
    response_model=InstalledSkillResponse,
    responses={
        400: {"description": "Failed to fetch skill source"},
        409: {"description": "Skill already installed (use force=true)"},
        422: {"description": "Invalid skill (missing SKILL.md, etc.)"},
    },
)
def install_skill_endpoint(request: InstallSkillRequest) -> InstalledSkillResponse:
    """Install a skill from a source.

    Installs a skill from a git URL, GitHub shorthand, or local path into
    the user's installed skills directory (~/.openhands/skills/installed/).

    Args:
        request: InstallSkillRequest containing source and options.

    Returns:
        InstalledSkillResponse with details about the installation.

    Raises:
        HTTPException 409: If skill is already installed and force=False.
        HTTPException 400: If fetching the skill source fails.
        HTTPException 422: If the skill is invalid.
    """
    try:
        info = service_install_skill(
            source=request.source,
            ref=request.ref,
            repo_path=request.repo_path,
            force=request.force,
        )
        return InstalledSkillResponse.from_skill_info(info)
    except FileExistsError:
        raise HTTPException(
            status_code=409,
            detail="Skill already installed. Use force=true to overwrite.",
        )
    except (SkillFetchError, ExtensionFetchError):
        raise HTTPException(
            status_code=400,
            detail="Failed to fetch skill source. Check that the source is valid.",
        )
    except SkillValidationError:
        raise HTTPException(
            status_code=422,
            detail="Invalid skill. Ensure the source contains a valid SKILL.md.",
        )


@skills_router.get("/installed", response_model=InstalledSkillsListResponse)
def list_installed_skills_endpoint() -> InstalledSkillsListResponse:
    """List all installed skills.

    Returns a list of all skills installed in the user's installed skills
    directory (~/.openhands/skills/installed/).

    Returns:
        InstalledSkillsListResponse containing list of installed skills.
    """
    skills = service_list_installed_skills()
    return InstalledSkillsListResponse(
        skills=[InstalledSkillResponse.from_skill_info(info) for info in skills]
    )


@skills_router.get(
    "/installed/{skill_name}",
    response_model=InstalledSkillResponse,
    responses={404: {"description": "Skill not installed"}},
)
def get_installed_skill_endpoint(skill_name: SkillNamePath) -> InstalledSkillResponse:
    """Get information about a specific installed skill.

    Args:
        skill_name: Name of the skill to get.

    Returns:
        InstalledSkillResponse with skill details.

    Raises:
        HTTPException 404: If the skill is not installed.
    """
    info = service_get_installed_skill(name=skill_name)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{skill_name}' is not installed",
        )
    return InstalledSkillResponse.from_skill_info(info)


@skills_router.patch(
    "/installed/{skill_name}",
    response_model=UpdateSkillStateResponse,
    responses={404: {"description": "Skill not installed"}},
)
def set_skill_enabled_endpoint(
    skill_name: SkillNamePath, request: UpdateSkillStateRequest
) -> UpdateSkillStateResponse:
    """Enable or disable an installed skill.

    Args:
        skill_name: Name of the skill to update.
        request: UpdateSkillStateRequest with enabled state.

    Returns:
        UpdateSkillStateResponse indicating new state.

    Raises:
        HTTPException 404: If the skill is not installed.
    """
    fn = service_enable_skill if request.enabled else service_disable_skill
    if not fn(name=skill_name):
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{skill_name}' is not installed",
        )

    return UpdateSkillStateResponse(
        name=skill_name,
        enabled=request.enabled,
    )


@skills_router.delete(
    "/installed/{skill_name}",
    response_model=UninstallSkillResponse,
    responses={404: {"description": "Skill not installed"}},
)
def uninstall_skill_endpoint(skill_name: SkillNamePath) -> UninstallSkillResponse:
    """Uninstall a skill by name.

    Removes a skill from the user's installed skills directory.

    Args:
        skill_name: Name of the skill to uninstall.

    Returns:
        UninstallSkillResponse with uninstall message.

    Raises:
        HTTPException 404: If the skill is not installed.
    """
    success = service_uninstall_skill(name=skill_name)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{skill_name}' is not installed",
        )
    return UninstallSkillResponse(
        message=f"Skill '{skill_name}' uninstalled",
    )


@skills_router.post(
    "/installed/{skill_name}/refresh",
    response_model=UpdateSkillResponse,
    responses={404: {"description": "Skill not installed"}},
)
def refresh_skill_endpoint(skill_name: SkillNamePath) -> UpdateSkillResponse:
    """Refresh an installed skill to the latest version.

    Re-fetches the skill from its original source and updates the installation.

    Args:
        skill_name: Name of the skill to refresh.

    Returns:
        UpdateSkillResponse with updated skill information.

    Raises:
        HTTPException 404: If the skill is not installed.
    """
    info = service_update_skill(name=skill_name)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{skill_name}' is not installed",
        )
    return UpdateSkillResponse(
        message=f"Skill '{skill_name}' updated",
        skill=InstalledSkillResponse.from_skill_info(info),
    )


@skills_router.get("/marketplace", response_model=MarketplaceCatalogResponse)
def get_marketplace_catalog() -> MarketplaceCatalogResponse:
    """Get the marketplace catalog with installation status.

    Returns a list of available skills from the OpenHands extensions
    repository marketplace, along with their installation status.

    This enables frontend applications to display a "Marketplace" tab
    with installable skills.

    Returns:
        MarketplaceCatalogResponse containing list of available skills.
    """
    return MarketplaceCatalogResponse(skills=service_get_marketplace_catalog())
