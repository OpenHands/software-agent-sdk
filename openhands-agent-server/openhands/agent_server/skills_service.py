"""Skills service for OpenHands Agent Server.

This module contains the business logic for skill loading and management,
keeping the router clean and focused on HTTP concerns.

Skill Sources:
- Public skills: GitHub OpenHands/extensions repository
- User skills: ~/.openhands/skills/ and ~/.openhands/microagents/
- Project skills: {workspace}/.openhands/skills/, .cursorrules, agents.md
- Organization skills: {org}/.openhands or {org}/openhands-config
- Sandbox skills: Exposed URLs from sandbox environment

Precedence (later overrides earlier):
sandbox < public < user < org < project
"""

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from openhands.sdk.logger import get_logger
from openhands.sdk.marketplace import Marketplace
from openhands.sdk.skills import (
    InstalledSkillInfo,
    Skill,
    disable_skill,
    enable_skill,
    get_installed_skill,
    install_skill,
    list_installed_skills,
    load_available_skills,
    uninstall_skill,
    update_skill,
)
from openhands.sdk.skills.skill import (
    DEFAULT_MARKETPLACE_PATH,
    PUBLIC_SKILLS_REF,
    PUBLIC_SKILLS_REPO,
    _invalidate_public_skills_cache,
    load_skills_from_dir,
)
from openhands.sdk.skills.utils import (
    get_skills_cache_dir,
    update_skills_repository,
)
from openhands.sdk.utils import sanitized_env
from openhands.sdk.utils.path import to_posix_path


if TYPE_CHECKING:
    from openhands.sdk.profiles import ACPAgentProfile, OpenHandsAgentProfile


logger = get_logger(__name__)


# Content template for sandbox work hosts skill
WORK_HOSTS_SKILL_CONTENT = (
    "The user has access to the following hosts for accessing "
    "a web application, each of which has a corresponding port:\n{hosts}"
)

# Prefix for sandbox URLs that should be exposed as work_hosts skill.
# URLs with names starting with this prefix represent web applications
# or services running in the sandbox that the agent should be aware of.
SANDBOX_WORKER_URL_PREFIX = "WORKER_"


@dataclass
class ExposedUrlData:
    """Internal representation of an exposed URL from the sandbox."""

    name: str
    url: str
    port: int


@dataclass
class SkillLoadResult:
    """Result of loading skills from all sources."""

    skills: list[Skill]
    sources: dict[str, int]


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

    # Determine a unique temporary directory for cloning. Two repos can share
    # the same org_name (e.g. {login}/.openhands and {login}/.agents), and
    # concurrent conversations may clone the same org, so the directory must be
    # unique per call rather than derived solely from org_name.
    if working_dir:
        base_dir = Path(working_dir) if isinstance(working_dir, str) else working_dir
        base_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f"_org_skills_{org_name}_", dir=base_dir)
        )
    else:
        temp_dir = Path(tempfile.mkdtemp(prefix=f"openhands_org_skills_{org_name}_"))

    try:
        # Clone the organization repository (shallow clone for efficiency)
        logger.info(f"Cloning organization skills repository for {org_name}")
        try:
            env = sanitized_env()
            env["GIT_TERMINAL_PROMPT"] = "0"
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
                env=env,
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

        logger.info("Loaded %d organization skills for %s", len(all_skills), org_name)

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
    exposed_urls: list[ExposedUrlData],
) -> Skill | None:
    """Create a skill from sandbox exposed URLs.

    This function creates a skill that informs the agent about web applications
    and services available in the sandbox environment via exposed ports/URLs.

    Only URLs with names starting with SANDBOX_WORKER_URL_PREFIX are included,
    as these represent web applications the agent should be aware of.

    Args:
        exposed_urls: List of ExposedUrlData objects containing name, url, and port.

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


def load_all_skills(
    load_public: bool = True,
    load_user: bool = True,
    load_project: bool = True,
    load_org: bool = True,
    project_dir: str | None = None,
    org_repos: list[tuple[str, str]] | None = None,
    sandbox_exposed_urls: list[ExposedUrlData] | None = None,
    marketplace_path: str | None = DEFAULT_MARKETPLACE_PATH,
) -> SkillLoadResult:
    """Load and merge skills from all configured sources.

    Skills are loaded from multiple sources and merged with the following
    precedence (later overrides earlier for duplicate names):
    1. Sandbox skills (lowest) - Exposed URLs from sandbox
    2. Public skills - From GitHub OpenHands/extensions repository
    3. User skills - From ~/.openhands/skills/
    4. Organization skills - From {org}/.openhands or equivalent
    5. Project skills (highest) - From {workspace}/.openhands/skills/

    Args:
        load_public: Whether to load public skills from OpenHands/extensions repo.
        load_user: Whether to load user skills from ~/.openhands/skills/.
        load_project: Whether to load project skills from workspace.
        load_org: Whether to load organization-level skills.
        project_dir: Workspace directory path for project skills.
        org_repos: Pre-authenticated (Git URL, org name) pairs for org skills.
            All repos are loaded and merged into the single org tier.
        sandbox_exposed_urls: List of exposed URLs from sandbox.
        marketplace_path: Relative marketplace JSON path for public skills.
            Pass None to load all public skills without marketplace filtering.

    Returns:
        SkillLoadResult containing merged skills and source counts.
    """
    sources: dict[str, int] = {}
    skill_lists: list[list[Skill]] = []

    # 1. Load sandbox skills (lowest precedence)
    sandbox_skills: list[Skill] = []
    if sandbox_exposed_urls:
        sandbox_skill = create_sandbox_skill(sandbox_exposed_urls)
        if sandbox_skill:
            sandbox_skills.append(sandbox_skill)
    sources["sandbox"] = len(sandbox_skills)
    skill_lists.append(sandbox_skills)

    # 2-3. Load public + user skills via helper (no project yet — org sits between)
    sdk_base = load_available_skills(
        work_dir=None,
        include_user=load_user,
        include_project=False,
        include_public=load_public,
        marketplace_path=marketplace_path,
    )
    sources["sdk_base"] = len(sdk_base)
    skill_lists.append(list(sdk_base.values()))

    # 4. Load organization skills (one or more repos merged into a single tier;
    # later repos override earlier ones on name collision).
    org_skills: list[Skill] = []
    if load_org and org_repos:
        per_repo_skills: list[list[Skill]] = []
        for org_repo_url, org_name in org_repos:
            try:
                per_repo_skills.append(
                    load_org_skills_from_url(
                        org_repo_url=org_repo_url,
                        org_name=org_name,
                    )
                )
            except Exception as e:
                logger.warning(
                    f"Failed to load organization skills for {org_name}: {e}"
                )
        org_skills = merge_skills(per_repo_skills)
        # Always log the count (including 0) so a failed/empty org load is
        # visible when org loading was requested.
        logger.info(f"Loaded {len(org_skills)} organization skills")
    sources["org"] = len(org_skills)
    skill_lists.append(org_skills)

    # 5. Load project skills (highest precedence)
    project_skills = load_available_skills(
        work_dir=project_dir if load_project else None,
        include_user=False,
        include_project=load_project,
        include_public=False,
    )
    sources["project"] = len(project_skills)
    skill_lists.append(list(project_skills.values()))

    # Merge all skills with precedence
    all_skills = merge_skills(skill_lists)

    logger.info("Loaded %d skills", len(all_skills))

    return SkillLoadResult(skills=all_skills, sources=sources)


def discover_profile_skills() -> list[Skill]:
    """Skill catalog for ``AgentProfile.skill_refs`` resolution (#3868).

    Returns the merged user + public skills — the deterministic sources of
    :func:`load_all_skills` that ``resolve_agent_profile`` filters by name.
    ``load_all_skills`` already absorbs and logs benign per-source failures, so
    this does not swallow errors: an unexpected failure propagates rather than
    silently resolving the profile to a zero-skill agent.

    Org / project skills need auth / workspace context not available at resolve
    time, so they are not in this catalog (a follow-up). Because the resolver
    now hard-fails dangling ``skill_refs`` (mirroring MCP), a profile that
    selects an org/project skill via the picker would fail launch here until the
    catalog is broadened — the SaaS app-server, which has that context, is
    expected to supply the fuller catalog the same way it does for the picker.
    """
    return list(
        load_all_skills(
            load_public=True,
            load_user=True,
            load_org=False,
            load_project=False,
        ).skills
    )


def discover_profile_skills_if_needed(
    profile: "OpenHandsAgentProfile | ACPAgentProfile",
) -> list[Skill] | None:
    """Discover the skill catalog a profile needs, or ``None`` to skip discovery.

    ``skill_refs == []`` selects no discovered skills, so the (potentially
    network-bound) discovery is skipped and the resolver receives ``None``. Any
    other value — ``None`` (all discovered) or a name list — needs the catalog.
    Centralizes the skip guard shared by conversation start and the dry-run.
    """
    if profile.skill_refs == []:
        return None
    return discover_profile_skills()


def sync_public_skills() -> tuple[bool, str]:
    """Force refresh of public skills from GitHub repository.

    This triggers a git pull on the cached skills repository to get
    the latest skills from the OpenHands/extensions repository.

    Returns:
        Tuple of (success: bool, message: str).
    """
    try:
        cache_dir = get_skills_cache_dir()
        result = update_skills_repository(
            PUBLIC_SKILLS_REPO, PUBLIC_SKILLS_REF, cache_dir
        )

        if result:
            _invalidate_public_skills_cache()
            return (True, "Skills repository synced successfully")
        else:
            return (False, "Failed to sync skills repository")
    except Exception as e:
        logger.warning(f"Failed to sync skills repository: {e}")
        return (False, f"Sync failed: {str(e)}")


# ---------------------------------------------------------------------------
# Installed Skills Management (CRUD Operations)
# ---------------------------------------------------------------------------


def service_install_skill(
    source: str,
    ref: str | None = None,
    repo_path: str | None = None,
    force: bool = False,
    installed_dir: Path | None = None,
) -> InstalledSkillInfo:
    """Install a skill from a source.

    Args:
        source: Skill source - git URL, GitHub shorthand, or local path.
            Supports formats like:
            - GitHub URL: https://github.com/OpenHands/extensions/tree/main/skills/github
            - GitHub shorthand: github:OpenHands/extensions/skills/github
            - Local path: /path/to/skill
        ref: Optional branch, tag, or commit to install.
        repo_path: Subdirectory path within the repository (for monorepos).
        force: If True, overwrite existing installation.
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/.

    Returns:
        InstalledSkillInfo with details about the installation.

    Raises:
        FileExistsError: If skill is already installed and force=False.
        SkillFetchError: If fetching the skill source fails.
        SkillValidationError: If the skill is invalid.
    """
    return install_skill(
        source=source,
        ref=ref,
        repo_path=repo_path,
        force=force,
        installed_dir=installed_dir,
    )


def service_uninstall_skill(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Uninstall a skill by name.

    Args:
        name: Name of the skill to uninstall.
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/.

    Returns:
        True if the skill was uninstalled, False if it wasn't installed.
    """
    return uninstall_skill(name=name, installed_dir=installed_dir)


def service_enable_skill(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Enable an installed skill by name.

    Args:
        name: Name of the skill to enable.
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/.

    Returns:
        True if the skill was enabled, False if it wasn't found.
    """
    return enable_skill(name=name, installed_dir=installed_dir)


def service_disable_skill(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Disable an installed skill by name.

    Args:
        name: Name of the skill to disable.
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/.

    Returns:
        True if the skill was disabled, False if it wasn't found.
    """
    return disable_skill(name=name, installed_dir=installed_dir)


def service_list_installed_skills(
    installed_dir: Path | None = None,
) -> list[InstalledSkillInfo]:
    """List all installed skills.

    Self-healing: reconciles metadata with what is on disk.

    Args:
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/.

    Returns:
        List of InstalledSkillInfo objects for all installed skills.
    """
    return list_installed_skills(installed_dir=installed_dir)


def service_get_installed_skill(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledSkillInfo | None:
    """Get information about a specific installed skill.

    Args:
        name: Name of the skill to get.
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/.

    Returns:
        InstalledSkillInfo if found, None otherwise.
    """
    return get_installed_skill(name=name, installed_dir=installed_dir)


def service_update_skill(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledSkillInfo | None:
    """Update an installed skill to the latest version.

    Args:
        name: Name of the skill to update.
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/.

    Returns:
        Updated InstalledSkillInfo if successful, None if skill not found.
    """
    return update_skill(name=name, installed_dir=installed_dir)


class MarketplaceSkillInfo(BaseModel):
    """Information about a skill in the marketplace catalog."""

    name: str
    description: str | None
    source: str
    installed: bool


# ---------------------------------------------------------------------------
# Marketplace catalog cache
# ---------------------------------------------------------------------------
# Each call to service_get_marketplace_catalog triggers a git fetch via
# update_skills_repository, which is a network-bound operation that takes
# multiple seconds. A short TTL cache avoids that hit on every tab open.
#
# Only the catalog structure (name, description, source) is cached; the
# `installed` field is always derived fresh from the local FS so that
# install/uninstall actions are reflected immediately.
#
# Thread safety: concurrent cache misses (cold start or TTL expiry) may
# trigger parallel git fetches, but each fetch is idempotent and produces
# the same result (last writer wins). For this low-traffic endpoint the
# thundering-herd risk is acceptable without an explicit lock.
#
# Type: (timestamp, list-of-(name, description, source)) or None
_CatalogEntry = tuple[str, str | None, str]
_catalog_cache: tuple[float, list[_CatalogEntry]] | None = None
_CATALOG_TTL_SECONDS = 300  # 5 minutes


def service_get_marketplace_catalog(
    marketplace_path: str = DEFAULT_MARKETPLACE_PATH,
    installed_dir: Path | None = None,
) -> list[MarketplaceSkillInfo]:
    """Get the marketplace catalog with installation status.

    Loads the marketplace JSON from the public extensions repository and
    enriches each entry with installation status.

    The catalog structure (name, description, source) is cached for
    _CATALOG_TTL_SECONDS to avoid a git fetch on every call. The
    ``installed`` field is always resolved fresh from the local FS.

    Args:
        marketplace_path: Relative path to marketplace JSON file.
            Defaults to marketplaces/default.json.
        installed_dir: Directory for installed skills to check status.
            Defaults to ~/.openhands/skills/installed/.

    Returns:
        List of MarketplaceSkillInfo with skill details and installation status.
    """
    global _catalog_cache

    now = monotonic()
    if _catalog_cache is not None and now - _catalog_cache[0] < _CATALOG_TTL_SECONDS:
        entries = _catalog_cache[1]
    else:
        entries = _fetch_catalog_entries(marketplace_path)
        _catalog_cache = (now, entries)

    # Always-fresh installed check — local FS scan, not a network call.
    installed_names = {
        s.name for s in service_list_installed_skills(installed_dir=installed_dir)
    }
    return [
        MarketplaceSkillInfo(
            name=name, description=desc, source=src, installed=name in installed_names
        )
        for name, desc, src in entries
    ]


def _fetch_catalog_entries(marketplace_path: str) -> list[_CatalogEntry]:
    """Fetch marketplace catalog entries from the public extensions repository.

    This is the slow path: it does a git fetch + reads the marketplace JSON.
    Results are cached by the caller.

    Returns:
        List of (name, description, source) tuples, or an empty list on error.
    """
    cache_dir = get_skills_cache_dir()
    repo_path = update_skills_repository(
        PUBLIC_SKILLS_REPO, PUBLIC_SKILLS_REF, cache_dir
    )

    if repo_path is None:
        logger.warning("Failed to access public skills repository")
        return []

    marketplace_file = repo_path / marketplace_path
    if not marketplace_file.exists():
        logger.warning(f"Marketplace file not found: {marketplace_file}")
        return []

    try:
        marketplace = Marketplace.load(repo_path)
    except (FileNotFoundError, ValueError) as e:
        # Fallback to loading from specific path
        try:
            with open(marketplace_file, encoding="utf-8") as f:
                data = json.load(f)
            marketplace = Marketplace.model_validate(
                {**data, "path": to_posix_path(repo_path)}
            )
        except (json.JSONDecodeError, ValidationError, OSError) as e2:
            logger.warning(f"Failed to load marketplace: {e}, {e2}")
            return []

    # Build catalog from plugins and skills.
    # Plugins take priority: if a name appears in both plugins and skills,
    # the plugin version is used (since plugins are added first).
    entries: dict[str, _CatalogEntry] = {}

    for plugin in marketplace.plugins:
        source, ref, subpath = marketplace.resolve_plugin_source(plugin)
        # Build full source string for marketplace catalog.
        # Format: "github:owner/repo@ref/path" - the SDK's install_skill
        # can parse this format, so frontends can pass it directly to the
        # install endpoint's source field.
        if ref:
            source = f"{source}@{ref}"
        if subpath:
            source = f"{source}/{subpath}"
        entries[plugin.name] = (plugin.name, plugin.description, source)

    for skill_entry in marketplace.skills:
        if skill_entry.name not in entries:
            entries[skill_entry.name] = (
                skill_entry.name,
                skill_entry.description,
                skill_entry.source,
            )

    return list(entries.values())
