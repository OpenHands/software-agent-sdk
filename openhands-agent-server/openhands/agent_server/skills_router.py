"""Skills router for OpenHands Agent Server.

This module defines the HTTP API endpoints for skill operations.
Business logic is delegated to skills_service.py.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from openhands.agent_server.skills_service import (
    ExposedUrlData,
    load_all_skills,
    sync_public_skills,
)


skills_router = APIRouter(prefix="/skills", tags=["Skills"])


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
    project_dir: str | None = Field(
        default=None, description="Workspace directory path for project skills"
    )
    org_config: OrgConfig | None = Field(
        default=None, description="Organization skills configuration"
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


@skills_router.post("", response_model=SkillsResponse)
def get_skills(request: SkillsRequest) -> SkillsResponse:
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

    org_repo_url = None
    org_name = None
    if request.org_config:
        org_repo_url = request.org_config.org_repo_url
        org_name = request.org_config.org_name

    # Call the service
    result = load_all_skills(
        load_public=request.load_public,
        load_user=request.load_user,
        load_project=request.load_project,
        load_org=request.load_org,
        project_dir=request.project_dir,
        org_repo_url=org_repo_url,
        org_name=org_name,
        sandbox_exposed_urls=sandbox_urls,
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
