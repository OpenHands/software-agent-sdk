import io
import re
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, ClassVar, Union

import frontmatter
from fastmcp.mcp_config import MCPConfig
from pydantic import BaseModel, Field, field_validator, model_validator

from openhands.sdk.context.skills.exceptions import SkillValidationError
from openhands.sdk.context.skills.trigger import (
    KeywordTrigger,
    TaskTrigger,
)
from openhands.sdk.context.skills.types import InputMetadata
from openhands.sdk.logger import get_logger
from openhands.sdk.utils import maybe_truncate


logger = get_logger(__name__)

# Maximum characters for third-party skill files (e.g., AGENTS.md, CLAUDE.md, GEMINI.md)
# These files are always active, so we want to keep them reasonably sized
THIRD_PARTY_SKILL_MAX_CHARS = 10_000

# Regex pattern for valid AgentSkills names
# - 1-64 characters
# - Lowercase alphanumeric + hyphens only (a-z, 0-9, -)
# - Must not start or end with hyphen
# - Must not contain consecutive hyphens (--)
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def find_skill_md(skill_dir: Path) -> Path | None:
    """Find SKILL.md file in a directory (case-insensitive).

    Args:
        skill_dir: Path to the skill directory to search.

    Returns:
        Path to SKILL.md if found, None otherwise.
    """
    if not skill_dir.is_dir():
        return None
    for item in skill_dir.iterdir():
        if item.is_file() and item.name.lower() == "skill.md":
            return item
    return None


def validate_skill_name(name: str, directory_name: str | None = None) -> list[str]:
    """Validate skill name according to AgentSkills spec.

    Args:
        name: The skill name to validate.
        directory_name: Optional directory name to check for match.

    Returns:
        List of validation error messages (empty if valid).
    """
    errors = []

    if not name:
        errors.append("Name cannot be empty")
        return errors

    if len(name) > 64:
        errors.append(f"Name exceeds 64 characters: {len(name)}")

    if not SKILL_NAME_PATTERN.match(name):
        errors.append(
            "Name must be lowercase alphanumeric with single hyphens "
            "(e.g., 'my-skill', 'pdf-tools')"
        )

    if directory_name and name != directory_name:
        errors.append(f"Name '{name}' does not match directory '{directory_name}'")

    return errors


# Union type for all trigger types
TriggerType = Annotated[
    KeywordTrigger | TaskTrigger,
    Field(discriminator="type"),
]


class Skill(BaseModel):
    """A skill provides specialized knowledge or functionality.

    Skills use triggers to determine when they should be activated:
    - None: Always active, for repository-specific guidelines
    - KeywordTrigger: Activated when keywords appear in user messages
    - TaskTrigger: Activated for specific tasks, may require user input

    This model supports both OpenHands-specific fields and AgentSkills standard
    fields (https://agentskills.io/specification) for cross-platform compatibility.
    """

    name: str
    content: str
    trigger: TriggerType | None = Field(
        default=None,
        description=(
            "Skills use triggers to determine when they should be activated. "
            "None implies skill is always active. "
            "Other implementations include KeywordTrigger (activated by a "
            "keyword in a Message) and TaskTrigger (activated by specific tasks "
            "and may require user input)"
        ),
    )
    source: str | None = Field(
        default=None,
        description=(
            "The source path or identifier of the skill. "
            "When it is None, it is treated as a programmatically defined skill."
        ),
    )
    mcp_tools: dict | None = Field(
        default=None,
        description=(
            "MCP tools configuration for the skill (repo skills only). "
            "It should conform to the MCPConfig schema: "
            "https://gofastmcp.com/clients/client#configuration-format"
        ),
    )
    inputs: list[InputMetadata] = Field(
        default_factory=list,
        description="Input metadata for the skill (task skills only)",
    )

    # AgentSkills standard fields (https://agentskills.io/specification)
    description: str | None = Field(
        default=None,
        description=(
            "A brief description of what the skill does and when to use it. "
            "AgentSkills standard field (max 1024 characters)."
        ),
    )
    license: str | None = Field(
        default=None,
        description=(
            "The license under which the skill is distributed. "
            "AgentSkills standard field (e.g., 'Apache-2.0', 'MIT')."
        ),
    )
    compatibility: str | None = Field(
        default=None,
        description=(
            "Environment requirements or compatibility notes for the skill. "
            "AgentSkills standard field (e.g., 'Requires git and docker')."
        ),
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description=(
            "Arbitrary key-value metadata for the skill. "
            "AgentSkills standard field for extensibility."
        ),
    )
    allowed_tools: list[str] | None = Field(
        default=None,
        description=(
            "List of pre-approved tools for this skill. "
            "AgentSkills standard field (parsed from space-delimited string)."
        ),
    )

    PATH_TO_THIRD_PARTY_SKILL_NAME: ClassVar[dict[str, str]] = {
        ".cursorrules": "cursorrules",
        "agents.md": "agents",
        "agent.md": "agents",
        "claude.md": "claude",
        "gemini.md": "gemini",
    }

    @classmethod
    def _handle_third_party(cls, path: Path, file_content: str) -> Union["Skill", None]:
        # Determine the agent name based on file type
        skill_name = cls.PATH_TO_THIRD_PARTY_SKILL_NAME.get(path.name.lower())

        # Create Skill with None trigger (always active) if we recognized the file type
        if skill_name is not None:
            # Truncate content if it exceeds the limit
            # Third-party files are always active, so we want to keep them
            # reasonably sized
            truncated_content = maybe_truncate(
                file_content,
                truncate_after=THIRD_PARTY_SKILL_MAX_CHARS,
                truncate_notice=(
                    f"\n\n<TRUNCATED><NOTE>The file {path} exceeded the "
                    f"maximum length ({THIRD_PARTY_SKILL_MAX_CHARS} "
                    f"characters) and has been truncated. Only the "
                    f"beginning and end are shown. You can read the full "
                    f"file if needed.</NOTE>\n\n"
                ),
            )

            if len(file_content) > THIRD_PARTY_SKILL_MAX_CHARS:
                logger.warning(
                    f"Third-party skill file {path} ({len(file_content)} chars) "
                    f"exceeded limit ({THIRD_PARTY_SKILL_MAX_CHARS} chars), truncating"
                )

            return Skill(
                name=skill_name,
                content=truncated_content,
                source=str(path),
                trigger=None,
            )

        return None

    @classmethod
    def _parse_agentskills_fields(cls, metadata_dict: dict) -> dict:
        """Parse AgentSkills standard fields from frontmatter metadata.

        Args:
            metadata_dict: The frontmatter metadata dictionary.

        Returns:
            Dictionary with AgentSkills fields (description, license,
            compatibility, metadata, allowed_tools).
        """
        agentskills_fields: dict = {}

        # Parse description (string, max 1024 chars per spec)
        if "description" in metadata_dict:
            desc = metadata_dict["description"]
            if isinstance(desc, str):
                agentskills_fields["description"] = desc
            else:
                raise SkillValidationError("description must be a string")

        # Parse license (string)
        if "license" in metadata_dict:
            lic = metadata_dict["license"]
            if isinstance(lic, str):
                agentskills_fields["license"] = lic
            else:
                raise SkillValidationError("license must be a string")

        # Parse compatibility (string)
        if "compatibility" in metadata_dict:
            compat = metadata_dict["compatibility"]
            if isinstance(compat, str):
                agentskills_fields["compatibility"] = compat
            else:
                raise SkillValidationError("compatibility must be a string")

        # Parse metadata (dict[str, str])
        if "metadata" in metadata_dict:
            meta = metadata_dict["metadata"]
            if isinstance(meta, dict):
                # Convert all values to strings for consistency
                agentskills_fields["metadata"] = {
                    str(k): str(v) for k, v in meta.items()
                }
            else:
                raise SkillValidationError("metadata must be a dictionary")

        # Parse allowed-tools (space-delimited string or list)
        # AgentSkills spec uses "allowed-tools" with hyphen
        allowed_tools_key = (
            "allowed-tools"
            if "allowed-tools" in metadata_dict
            else ("allowed_tools" if "allowed_tools" in metadata_dict else None)
        )
        if allowed_tools_key:
            tools = metadata_dict[allowed_tools_key]
            if isinstance(tools, str):
                # Parse space-delimited string
                agentskills_fields["allowed_tools"] = tools.split()
            elif isinstance(tools, list):
                agentskills_fields["allowed_tools"] = [str(t) for t in tools]
            else:
                raise SkillValidationError("allowed-tools must be a string or list")

        return agentskills_fields

    @classmethod
    def load(
        cls,
        path: str | Path,
        skill_dir: Path | None = None,
        file_content: str | None = None,
        directory_name: str | None = None,
        validate_name: bool = False,
    ) -> "Skill":
        """Load a skill from a markdown file with frontmatter.

        The agent's name is derived from its path relative to the skill_dir,
        or from the directory name for AgentSkills-style SKILL.md files.

        Supports both OpenHands-specific frontmatter fields and AgentSkills
        standard fields (https://agentskills.io/specification).

        Args:
            path: Path to the skill file.
            skill_dir: Base directory for skills (used to derive relative names).
            file_content: Optional file content (if not provided, reads from path).
            directory_name: For SKILL.md files, the parent directory name.
                Used to derive skill name and validate name matches directory.
            validate_name: If True, validate the skill name according to
                AgentSkills spec and raise SkillValidationError if invalid.
        """
        path = Path(path) if isinstance(path, str) else path

        # Calculate derived name from relative path if skill_dir is provided
        skill_name: str | None = None

        # For SKILL.md files, use directory name as the skill name
        if directory_name is not None:
            skill_name = directory_name
        elif skill_dir is not None:
            # Special handling for files which are not in skill_dir
            skill_name = cls.PATH_TO_THIRD_PARTY_SKILL_NAME.get(
                path.name.lower()
            ) or str(path.relative_to(skill_dir).with_suffix(""))
        else:
            skill_name = path.stem

        # Only load directly from path if file_content is not provided
        if file_content is None:
            with open(path) as f:
                file_content = f.read()

        # Handle third-party agent instruction files
        third_party_agent = cls._handle_third_party(path, file_content)
        if third_party_agent is not None:
            return third_party_agent

        file_io = io.StringIO(file_content)
        loaded = frontmatter.load(file_io)
        content = loaded.content

        # Handle case where there's no frontmatter or empty frontmatter
        metadata_dict = loaded.metadata or {}

        # Use name from frontmatter if provided, otherwise use derived name
        agent_name = str(metadata_dict.get("name", skill_name))

        # Validate skill name if requested
        if validate_name:
            name_errors = validate_skill_name(agent_name, directory_name)
            if name_errors:
                raise SkillValidationError(
                    f"Invalid skill name '{agent_name}': {'; '.join(name_errors)}"
                )

        # Parse AgentSkills standard fields
        agentskills_fields = cls._parse_agentskills_fields(metadata_dict)

        # Get trigger keywords from metadata
        keywords = metadata_dict.get("triggers", [])
        if not isinstance(keywords, list):
            raise SkillValidationError("Triggers must be a list of strings")

        # Infer the trigger type:
        # 1. If inputs exist -> TaskTrigger
        # 2. If keywords exist -> KeywordTrigger
        # 3. Else (no keywords) -> None (always active)
        if "inputs" in metadata_dict:
            # Add a trigger for the agent name if not already present
            trigger_keyword = f"/{agent_name}"
            if trigger_keyword not in keywords:
                keywords.append(trigger_keyword)
            inputs_raw = metadata_dict.get("inputs", [])
            if not isinstance(inputs_raw, list):
                raise SkillValidationError("inputs must be a list")
            inputs: list[InputMetadata] = [
                InputMetadata.model_validate(i) for i in inputs_raw
            ]
            return Skill(
                name=agent_name,
                content=content,
                source=str(path),
                trigger=TaskTrigger(triggers=keywords),
                inputs=inputs,
                **agentskills_fields,
            )

        elif metadata_dict.get("triggers", None):
            return Skill(
                name=agent_name,
                content=content,
                source=str(path),
                trigger=KeywordTrigger(keywords=keywords),
                **agentskills_fields,
            )
        else:
            # No triggers, default to None (always active)
            mcp_tools = metadata_dict.get("mcp_tools")
            if not isinstance(mcp_tools, dict | None):
                raise SkillValidationError("mcp_tools must be a dictionary or None")
            return Skill(
                name=agent_name,
                content=content,
                source=str(path),
                trigger=None,
                mcp_tools=mcp_tools,
                **agentskills_fields,
            )

    # Field-level validation for mcp_tools
    @field_validator("mcp_tools")
    @classmethod
    def _validate_mcp_tools(cls, v: dict | None, _info):
        if v is None:
            return v
        if isinstance(v, dict):
            try:
                MCPConfig.model_validate(v)
            except Exception as e:
                raise SkillValidationError(f"Invalid MCPConfig dictionary: {e}") from e
        return v

    @model_validator(mode="after")
    def _append_missing_variables_prompt(self):
        """Append a prompt to ask for missing variables after model construction."""
        # Only apply to task skills
        if not isinstance(self.trigger, TaskTrigger):
            return self

        # If no variables and no inputs, nothing to do
        if not self.requires_user_input() and not self.inputs:
            return self

        prompt = (
            "\n\nIf the user didn't provide any of these variables, ask the user to "
            "provide them first before the agent can proceed with the task."
        )

        # Avoid duplicating the prompt if content already includes it
        if self.content and prompt not in self.content:
            self.content += prompt

        return self

    def match_trigger(self, message: str) -> str | None:
        """Match a trigger in the message.

        Returns the first trigger that matches the message, or None if no match.
        Only applies to KeywordTrigger and TaskTrigger types.
        """
        if isinstance(self.trigger, KeywordTrigger):
            message_lower = message.lower()
            for keyword in self.trigger.keywords:
                if keyword.lower() in message_lower:
                    return keyword
        elif isinstance(self.trigger, TaskTrigger):
            message_lower = message.lower()
            for trigger_str in self.trigger.triggers:
                if trigger_str.lower() in message_lower:
                    return trigger_str
        return None

    def extract_variables(self, content: str) -> list[str]:
        """Extract variables from the content.

        Variables are in the format ${variable_name}.
        """
        pattern = r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}"
        matches = re.findall(pattern, content)
        return matches

    def requires_user_input(self) -> bool:
        """Check if this skill requires user input.

        Returns True if the content contains variables in the format ${variable_name}.
        """
        # Check if the content contains any variables
        variables = self.extract_variables(self.content)
        logger.debug(f"This skill requires user input: {variables}")
        return len(variables) > 0


def load_skills_from_dir(
    skill_dir: str | Path,
    validate_names: bool = False,
) -> tuple[dict[str, Skill], dict[str, Skill]]:
    """Load all skills from the given directory.

    Supports both formats:
    - OpenHands format: skills/*.md files
    - AgentSkills format: skills/skill-name/SKILL.md directories

    Note, legacy repo instructions will not be loaded here.

    Args:
        skill_dir: Path to the skills directory (e.g. .openhands/skills)
        validate_names: If True, validate skill names according to AgentSkills
            spec and raise SkillValidationError for invalid names.

    Returns:
        Tuple of (repo_skills, knowledge_skills) dictionaries.
        repo_skills have trigger=None, knowledge_skills have KeywordTrigger
        or TaskTrigger.
    """
    if isinstance(skill_dir, str):
        skill_dir = Path(skill_dir)

    repo_skills: dict[str, Skill] = {}
    knowledge_skills: dict[str, Skill] = {}

    # Load all agents from skills directory
    logger.debug(f"Loading agents from {skill_dir}")

    # Always check for .cursorrules and AGENTS.md files in repo root
    special_files: list[Path] = []
    repo_root = skill_dir.parent.parent

    # Check for third party rules: .cursorrules, AGENTS.md, etc
    for filename in Skill.PATH_TO_THIRD_PARTY_SKILL_NAME.keys():
        for variant in [filename, filename.lower(), filename.upper()]:
            if (repo_root / variant).exists():
                special_files.append(repo_root / variant)
                break  # Only add the first one found to avoid duplicates

    # Track directories with SKILL.md to avoid double-loading
    skill_md_dirs: set[Path] = set()

    # Collect AgentSkills-style directories (skill-name/SKILL.md)
    skill_md_files: list[tuple[Path, str]] = []  # (path, directory_name)
    if skill_dir.exists():
        for subdir in skill_dir.iterdir():
            if subdir.is_dir():
                skill_md = find_skill_md(subdir)
                if skill_md:
                    skill_md_files.append((skill_md, subdir.name))
                    skill_md_dirs.add(subdir)

    # Collect .md files from skills directory (excluding SKILL.md files)
    md_files: list[Path] = []
    if skill_dir.exists():
        for f in skill_dir.rglob("*.md"):
            # Skip README.md
            if f.name == "README.md":
                continue
            # Skip SKILL.md files (already collected above)
            if f.name.lower() == "skill.md":
                continue
            # Skip files in directories that have SKILL.md
            if any(f.is_relative_to(d) for d in skill_md_dirs):
                continue
            md_files.append(f)

    def add_skill(skill: Skill) -> None:
        """Add skill to appropriate dictionary."""
        if skill.trigger is None:
            repo_skills[skill.name] = skill
        else:
            knowledge_skills[skill.name] = skill

    # Process special files (third-party rules)
    for file in special_files:
        try:
            skill = Skill.load(file, skill_dir)
            add_skill(skill)
        except SkillValidationError as e:
            error_msg = f"Error loading skill from {file}: {str(e)}"
            raise SkillValidationError(error_msg) from e
        except Exception as e:
            error_msg = f"Error loading skill from {file}: {str(e)}"
            raise ValueError(error_msg) from e

    # Process AgentSkills-style SKILL.md directories
    for skill_md_path, dir_name in skill_md_files:
        try:
            skill = Skill.load(
                skill_md_path,
                skill_dir,
                directory_name=dir_name,
                validate_name=validate_names,
            )
            add_skill(skill)
        except SkillValidationError as e:
            error_msg = f"Error loading skill from {skill_md_path}: {str(e)}"
            raise SkillValidationError(error_msg) from e
        except Exception as e:
            error_msg = f"Error loading skill from {skill_md_path}: {str(e)}"
            raise ValueError(error_msg) from e

    # Process regular .md files
    for file in md_files:
        try:
            skill = Skill.load(file, skill_dir, validate_name=validate_names)
            add_skill(skill)
        except SkillValidationError as e:
            error_msg = f"Error loading skill from {file}: {str(e)}"
            raise SkillValidationError(error_msg) from e
        except Exception as e:
            error_msg = f"Error loading skill from {file}: {str(e)}"
            raise ValueError(error_msg) from e

    logger.debug(
        f"Loaded {len(repo_skills) + len(knowledge_skills)} skills: "
        f"{[*repo_skills.keys(), *knowledge_skills.keys()]}"
    )
    return repo_skills, knowledge_skills


# Default user skills directories (in order of priority)
USER_SKILLS_DIRS = [
    Path.home() / ".openhands" / "skills",
    Path.home() / ".openhands" / "microagents",  # Legacy support
]


def load_user_skills() -> list[Skill]:
    """Load skills from user's home directory.

    Searches for skills in ~/.openhands/skills/ and ~/.openhands/microagents/
    (legacy). Skills from both directories are merged, with skills/ taking
    precedence for duplicate names.

    Returns:
        List of Skill objects loaded from user directories.
        Returns empty list if no skills found or loading fails.
    """
    all_skills = []
    seen_names = set()

    for skills_dir in USER_SKILLS_DIRS:
        if not skills_dir.exists():
            logger.debug(f"User skills directory does not exist: {skills_dir}")
            continue

        try:
            logger.debug(f"Loading user skills from {skills_dir}")
            repo_skills, knowledge_skills = load_skills_from_dir(skills_dir)

            # Merge repo and knowledge skills
            for skills_dict in [repo_skills, knowledge_skills]:
                for name, skill in skills_dict.items():
                    if name not in seen_names:
                        all_skills.append(skill)
                        seen_names.add(name)
                    else:
                        logger.warning(
                            f"Skipping duplicate skill '{name}' from {skills_dir}"
                        )

        except Exception as e:
            logger.warning(f"Failed to load user skills from {skills_dir}: {str(e)}")

    logger.debug(
        f"Loaded {len(all_skills)} user skills: {[s.name for s in all_skills]}"
    )
    return all_skills


def load_project_skills(work_dir: str | Path) -> list[Skill]:
    """Load skills from project-specific directories.

    Searches for skills in {work_dir}/.openhands/skills/ and
    {work_dir}/.openhands/microagents/ (legacy). Skills from both
    directories are merged, with skills/ taking precedence for
    duplicate names.

    Args:
        work_dir: Path to the project/working directory.

    Returns:
        List of Skill objects loaded from project directories.
        Returns empty list if no skills found or loading fails.
    """
    if isinstance(work_dir, str):
        work_dir = Path(work_dir)

    all_skills = []
    seen_names = set()

    # Load project-specific skills from .openhands/skills and legacy microagents
    project_skills_dirs = [
        work_dir / ".openhands" / "skills",
        work_dir / ".openhands" / "microagents",  # Legacy support
    ]

    for project_skills_dir in project_skills_dirs:
        if not project_skills_dir.exists():
            logger.debug(
                f"Project skills directory does not exist: {project_skills_dir}"
            )
            continue

        try:
            logger.debug(f"Loading project skills from {project_skills_dir}")
            repo_skills, knowledge_skills = load_skills_from_dir(project_skills_dir)

            # Merge repo and knowledge skills
            for skills_dict in [repo_skills, knowledge_skills]:
                for name, skill in skills_dict.items():
                    if name not in seen_names:
                        all_skills.append(skill)
                        seen_names.add(name)
                    else:
                        logger.warning(
                            f"Skipping duplicate skill '{name}' from "
                            f"{project_skills_dir}"
                        )

        except Exception as e:
            logger.warning(
                f"Failed to load project skills from {project_skills_dir}: {str(e)}"
            )

    logger.debug(
        f"Loaded {len(all_skills)} project skills: {[s.name for s in all_skills]}"
    )
    return all_skills


# Public skills repository configuration
PUBLIC_SKILLS_REPO = "https://github.com/OpenHands/skills"
PUBLIC_SKILLS_BRANCH = "main"


def _get_skills_cache_dir() -> Path:
    """Get the local cache directory for public skills repository.

    Returns:
        Path to the skills cache directory (~/.openhands/cache/skills).
    """
    cache_dir = Path.home() / ".openhands" / "cache" / "skills"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _update_skills_repository(
    repo_url: str,
    branch: str,
    cache_dir: Path,
) -> Path | None:
    """Clone or update the local skills repository.

    Args:
        repo_url: URL of the skills repository.
        branch: Branch name to use.
        cache_dir: Directory where the repository should be cached.

    Returns:
        Path to the local repository if successful, None otherwise.
    """
    repo_path = cache_dir / "public-skills"

    try:
        if repo_path.exists() and (repo_path / ".git").exists():
            logger.debug(f"Updating skills repository at {repo_path}")
            try:
                subprocess.run(
                    ["git", "fetch", "origin"],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                subprocess.run(
                    ["git", "reset", "--hard", f"origin/{branch}"],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
                logger.debug("Skills repository updated successfully")
            except subprocess.TimeoutExpired:
                logger.warning("Git pull timed out, using existing cached repository")
            except subprocess.CalledProcessError as e:
                logger.warning(
                    f"Failed to update repository: {e.stderr.decode()}, "
                    f"using existing cached version"
                )
        else:
            logger.info(f"Cloning public skills repository from {repo_url}")
            if repo_path.exists():
                shutil.rmtree(repo_path)

            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    branch,
                    repo_url,
                    str(repo_path),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            logger.debug(f"Skills repository cloned to {repo_path}")

        return repo_path

    except subprocess.TimeoutExpired:
        logger.warning(f"Git operation timed out for {repo_url}")
        return None
    except subprocess.CalledProcessError as e:
        logger.warning(
            f"Failed to clone/update repository {repo_url}: {e.stderr.decode()}"
        )
        return None
    except Exception as e:
        logger.warning(f"Error managing skills repository: {str(e)}")
        return None


def load_public_skills(
    repo_url: str = PUBLIC_SKILLS_REPO,
    branch: str = PUBLIC_SKILLS_BRANCH,
) -> list[Skill]:
    """Load skills from the public OpenHands skills repository.

    This function maintains a local git clone of the public skills registry at
    https://github.com/OpenHands/skills. On first run, it clones the repository
    to ~/.openhands/skills-cache/. On subsequent runs, it pulls the latest changes
    to keep the skills up-to-date. This approach is more efficient than fetching
    individual files via HTTP.

    Args:
        repo_url: URL of the skills repository. Defaults to the official
            OpenHands skills repository.
        branch: Branch name to load skills from. Defaults to 'main'.

    Returns:
        List of Skill objects loaded from the public repository.
        Returns empty list if loading fails.

    Example:
        >>> from openhands.sdk.context import AgentContext
        >>> from openhands.sdk.context.skills import load_public_skills
        >>>
        >>> # Load public skills
        >>> public_skills = load_public_skills()
        >>>
        >>> # Use with AgentContext
        >>> context = AgentContext(skills=public_skills)
    """
    all_skills = []

    try:
        # Get or update the local repository
        cache_dir = _get_skills_cache_dir()
        repo_path = _update_skills_repository(repo_url, branch, cache_dir)

        if repo_path is None:
            logger.warning("Failed to access public skills repository")
            return all_skills

        # Load skills from the local repository
        skills_dir = repo_path / "skills"
        if not skills_dir.exists():
            logger.warning(f"Skills directory not found in repository: {skills_dir}")
            return all_skills

        # Find all .md files in the skills directory
        md_files = [f for f in skills_dir.rglob("*.md") if f.name != "README.md"]

        logger.info(f"Found {len(md_files)} skill files in public skills repository")

        # Load each skill file
        for skill_file in md_files:
            try:
                skill = Skill.load(
                    path=skill_file,
                    skill_dir=repo_path,
                )
                all_skills.append(skill)
                logger.debug(f"Loaded public skill: {skill.name}")
            except Exception as e:
                logger.warning(f"Failed to load skill from {skill_file.name}: {str(e)}")
                continue

    except Exception as e:
        logger.warning(f"Failed to load public skills from {repo_url}: {str(e)}")

    logger.info(
        f"Loaded {len(all_skills)} public skills: {[s.name for s in all_skills]}"
    )
    return all_skills
