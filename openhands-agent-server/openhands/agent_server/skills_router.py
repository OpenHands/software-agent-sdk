"""Skills router for OpenHands Agent Server.

This module centralizes all skill loading logic in the agent-server,
making it the canonical source for skills resolution.

Skill Sources:
- Public skills: GitHub OpenHands/skills repository
- User skills: ~/.openhands/skills/ and ~/.openhands/microagents/
- Project skills: {workspace}/.openhands/skills/, .cursorrules, agents.md
- Organization skills: {org}/.openhands or {org}/openhands-config
- Sandbox skills: Exposed URLs from sandbox environment

Precedence (later overrides earlier):
sandbox < public < user < org < project
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from openhands.sdk.context.skills import (
    Skill,
    load_project_skills,
    load_public_skills,
    load_user_skills,
)
from openhands.sdk.context.skills.skill import (
    PUBLIC_SKILLS_BRANCH,
    PUBLIC_SKILLS_REPO,
    load_skills_from_dir,
)
from openhands.sdk.context.skills.trigger import KeywordTrigger, TaskTrigger
from openhands.sdk.context.skills.utils import (
    get_skills_cache_dir,
    update_skills_repository,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

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
        default=True, description="Load public skills from OpenHands/skills repo"
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
    type: Literal["repo", "knowledge", "agent"]
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


# Content template for sandbox work hosts skill
WORK_HOSTS_SKILL_CONTENT = (
    "The user has access to the following hosts for accessing "
    "a web application, each of which has a corresponding port:\n{hosts}"
)

# Prefix for sandbox URLs that should be exposed as work_hosts skill.
# URLs with names starting with this prefix represent web applications
# or services running in the sandbox that the agent should be aware of.
SANDBOX_WORKER_URL_PREFIX = "WORKER_"


def load_org_skills_from_url(
    org_repo_url: str,
    org_name: str,
    working_dir: str | Path | None = None,
) -> list[Skill]:
    """Load skills from an organization repository.

    This function clones an organization-level skills repository to a temporary
    directory, loads skills from the skills/ and microagents/ directories, and
    then cleans up the temporary directory.

    The org_repo_url should be a pre-authenticated Git URL (e.g., containing
    credentials or tokens) as provided by the app-server.

    Note:
        This is a blocking I/O operation that may take up to 120 seconds due to
        the git clone timeout. When called from FastAPI endpoints defined with
        `def` (not `async def`), FastAPI automatically runs this in a thread
        pool to avoid blocking the event loop. Do not call this function
        directly from async code without wrapping it in asyncio.to_thread().

    Args:
        org_repo_url: Pre-authenticated Git URL for the organization repository.
            This should be a full Git URL that includes authentication.
        org_name: Name of the organization (used for temp directory naming).
        working_dir: Optional working directory for git operations. If None,
            uses a subdirectory of the system temp directory.

    Returns:
        List of Skill objects loaded from the organization repository.
        Returns empty list if the repository doesn't exist or loading fails.
    """
    all_skills: list[Skill] = []

    # Determine the temporary directory for cloning
    if working_dir:
        base_dir = Path(working_dir) if isinstance(working_dir, str) else working_dir
        temp_dir = base_dir / f"_org_skills_{org_name}"
    else:
        temp_dir = Path(tempfile.gettempdir()) / f"openhands_org_skills_{org_name}"

    try:
        # Clean up any existing temp directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        # Clone the organization repository (shallow clone for efficiency)
        logger.info(f"Cloning organization skills repository for {org_name}")
        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    org_repo_url,
                    str(temp_dir),
                ],
                check=True,
                capture_output=True,
                timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.CalledProcessError:
            # Repository doesn't exist or access denied - this is expected.
            # Note: We intentionally don't log stderr as it may contain credentials.
            logger.debug(
                f"Organization repository not found or access denied for {org_name}"
            )
            return all_skills
        except subprocess.TimeoutExpired:
            logger.warning(
                f"Git clone timed out for organization repository {org_name}"
            )
            return all_skills

        logger.debug(f"Successfully cloned org repository to {temp_dir}")

        # Load skills from skills/ directory (preferred)
        skills_dir = temp_dir / "skills"
        if skills_dir.exists():
            try:
                repo_skills, knowledge_skills, agent_skills = load_skills_from_dir(
                    skills_dir
                )
                for skills_dict in [repo_skills, knowledge_skills, agent_skills]:
                    all_skills.extend(skills_dict.values())
                logger.debug(
                    f"Loaded {len(all_skills)} skills from org skills/ directory"
                )
            except Exception as e:
                logger.warning(f"Failed to load skills from {skills_dir}: {e}")

        # Load skills from microagents/ directory (legacy support)
        microagents_dir = temp_dir / "microagents"
        if microagents_dir.exists():
            seen_names = {s.name for s in all_skills}
            try:
                repo_skills, knowledge_skills, agent_skills = load_skills_from_dir(
                    microagents_dir
                )
                for skills_dict in [repo_skills, knowledge_skills, agent_skills]:
                    for name, skill in skills_dict.items():
                        if name not in seen_names:
                            all_skills.append(skill)
                            seen_names.add(name)
                        else:
                            logger.debug(
                                f"Skipping duplicate org skill '{name}' "
                                "from microagents/"
                            )
            except Exception as e:
                logger.warning(f"Failed to load skills from {microagents_dir}: {e}")

        logger.info(
            f"Loaded {len(all_skills)} organization skills for {org_name}: "
            f"{[s.name for s in all_skills]}"
        )

    except Exception as e:
        logger.warning(f"Failed to load organization skills for {org_name}: {e}")

    finally:
        # Clean up the temporary directory
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
                logger.debug(f"Cleaned up temp directory {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp directory {temp_dir}: {e}")

    return all_skills


def create_sandbox_skill(
    exposed_urls: list[ExposedUrl],
) -> Skill | None:
    """Create a skill from sandbox exposed URLs.

    This function creates a skill that informs the agent about web applications
    and services available in the sandbox environment via exposed ports/URLs.

    Only URLs with names starting with SANDBOX_WORKER_URL_PREFIX are included,
    as these represent web applications the agent should be aware of.

    Args:
        exposed_urls: List of ExposedUrl objects containing name, url, and port.

    Returns:
        A Skill object with work_hosts content if there are matching URLs,
        or None if no relevant URLs are provided.
    """
    if not exposed_urls:
        return None

    # Filter for URLs with the worker prefix
    worker_urls = [
        url for url in exposed_urls if url.name.startswith(SANDBOX_WORKER_URL_PREFIX)
    ]

    if not worker_urls:
        return None

    # Build the hosts content
    hosts_lines = []
    for url_info in worker_urls:
        hosts_lines.append(f"* {url_info.url} (port {url_info.port})")

    hosts_content = "\n".join(hosts_lines)
    content = WORK_HOSTS_SKILL_CONTENT.format(hosts=hosts_content)

    return Skill(
        name="work_hosts",
        content=content,
        trigger=None,  # Always active
        source=None,  # Programmatically generated
    )


def skill_to_info(skill: Skill) -> SkillInfo:
    """Convert a Skill object to SkillInfo for API response.

    Args:
        skill: The Skill object to convert.

    Returns:
        SkillInfo object with relevant fields extracted.
    """
    # Determine skill type
    if skill.is_agentskills_format:
        skill_type: Literal["repo", "knowledge", "agent"] = "agent"
    elif skill.trigger is None:
        skill_type = "repo"
    else:
        skill_type = "knowledge"

    # Extract triggers
    triggers: list[str] = []
    if isinstance(skill.trigger, KeywordTrigger):
        triggers = skill.trigger.keywords
    elif isinstance(skill.trigger, TaskTrigger):
        triggers = skill.trigger.triggers

    return SkillInfo(
        name=skill.name,
        type=skill_type,
        content=skill.content,
        triggers=triggers,
        source=skill.source,
        description=skill.description,
        is_agentskills_format=skill.is_agentskills_format,
    )


def merge_skills(skill_lists: list[list[Skill]]) -> list[Skill]:
    """Merge multiple skill lists with precedence.

    Later lists override earlier lists for duplicate names.

    Args:
        skill_lists: List of skill lists to merge in order of precedence.

    Returns:
        Merged list of skills with duplicates resolved.
    """
    skills_by_name: dict[str, Skill] = {}

    for skill_list in skill_lists:
        for skill in skill_list:
            if skill.name in skills_by_name:
                logger.info(
                    f"Overriding skill '{skill.name}' from earlier source "
                    "with later source"
                )
            skills_by_name[skill.name] = skill

    return list(skills_by_name.values())


@skills_router.post("", response_model=SkillsResponse)
def get_skills(request: SkillsRequest) -> SkillsResponse:
    """Load and merge skills from all configured sources.

    Skills are loaded from multiple sources and merged with the following
    precedence (later overrides earlier for duplicate names):
    1. Sandbox skills (lowest) - Exposed URLs from sandbox
    2. Public skills - From GitHub OpenHands/skills repository
    3. User skills - From ~/.openhands/skills/
    4. Organization skills - From {org}/.openhands or equivalent
    5. Project skills (highest) - From {workspace}/.openhands/skills/

    Args:
        request: SkillsRequest containing configuration for which sources to load.

    Returns:
        SkillsResponse containing merged skills and source counts.
    """
    sources: dict[str, int] = {}
    skill_lists: list[list[Skill]] = []

    # 1. Load sandbox skills (lowest precedence)
    sandbox_skills: list[Skill] = []
    if request.sandbox_config and request.sandbox_config.exposed_urls:
        sandbox_skill = create_sandbox_skill(request.sandbox_config.exposed_urls)
        if sandbox_skill:
            sandbox_skills.append(sandbox_skill)
    sources["sandbox"] = len(sandbox_skills)
    skill_lists.append(sandbox_skills)

    # 2. Load public skills
    public_skills: list[Skill] = []
    if request.load_public:
        try:
            public_skills = load_public_skills()
            logger.info(f"Loaded {len(public_skills)} public skills")
        except Exception as e:
            logger.warning(f"Failed to load public skills: {e}")
    sources["public"] = len(public_skills)
    skill_lists.append(public_skills)

    # 3. Load user skills
    user_skills: list[Skill] = []
    if request.load_user:
        try:
            user_skills = load_user_skills()
            logger.info(f"Loaded {len(user_skills)} user skills")
        except Exception as e:
            logger.warning(f"Failed to load user skills: {e}")
    sources["user"] = len(user_skills)
    skill_lists.append(user_skills)

    # 4. Load organization skills
    org_skills: list[Skill] = []
    if request.load_org and request.org_config:
        try:
            org_skills = load_org_skills_from_url(
                org_repo_url=request.org_config.org_repo_url,
                org_name=request.org_config.org_name,
            )
            logger.info(f"Loaded {len(org_skills)} organization skills")
        except Exception as e:
            logger.warning(f"Failed to load organization skills: {e}")
    sources["org"] = len(org_skills)
    skill_lists.append(org_skills)

    # 5. Load project skills (highest precedence)
    project_skills: list[Skill] = []
    if request.load_project and request.project_dir:
        try:
            project_skills = load_project_skills(request.project_dir)
            logger.info(f"Loaded {len(project_skills)} project skills")
        except Exception as e:
            logger.warning(f"Failed to load project skills: {e}")
    sources["project"] = len(project_skills)
    skill_lists.append(project_skills)

    # Merge all skills with precedence
    all_skills = merge_skills(skill_lists)

    # Convert to response format
    skills_info = [skill_to_info(skill) for skill in all_skills]

    logger.info(
        f"Returning {len(skills_info)} total skills: {[s.name for s in skills_info]}"
    )

    return SkillsResponse(skills=skills_info, sources=sources)


@skills_router.post("/sync", response_model=SyncResponse)
def sync_skills() -> SyncResponse:
    """Force refresh of public skills from GitHub repository.

    This triggers a git pull on the cached skills repository to get
    the latest skills from the OpenHands/skills repository.

    Returns:
        SyncResponse indicating success or failure.
    """
    try:
        cache_dir = get_skills_cache_dir()
        result = update_skills_repository(
            PUBLIC_SKILLS_REPO, PUBLIC_SKILLS_BRANCH, cache_dir
        )

        if result:
            return SyncResponse(
                status="success", message="Skills repository synced successfully"
            )
        else:
            return SyncResponse(
                status="error", message="Failed to sync skills repository"
            )
    except Exception as e:
        logger.warning(f"Failed to sync skills repository: {e}")
        return SyncResponse(status="error", message=f"Sync failed: {str(e)}")
