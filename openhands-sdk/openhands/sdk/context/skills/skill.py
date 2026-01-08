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

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def _parse_allowed_tools(cls, v: str | list | None) -> list[str] | None:
        """Parse allowed_tools from space-delimited string or list."""
        if v is None:
            return None
        if isinstance(v, str):
            return v.split()
        if isinstance(v, list):
            return [str(t) for t in v]
        raise SkillValidationError("allowed-tools must be a string or list")

    @field_validator("metadata", mode="before")
    @classmethod
    def _convert_metadata_values(cls, v: dict | None) -> dict[str, str] | None:
        """Convert metadata values to strings."""
        if v is None:
            return None
        if isinstance(v, dict):
            return {str(k): str(val) for k, val in v.items()}
        raise SkillValidationError("metadata must be a dictionary")

    @field_validator("mcp_tools")
    @classmethod
    def _validate_mcp_tools(cls, v: dict | None, _info):
        """Validate mcp_tools conforms to MCPConfig schema."""
        if v is None:
            return v
        if isinstance(v, dict):
            try:
                MCPConfig.model_validate(v)
            except Exception as e:
                raise SkillValidationError(f"Invalid MCPConfig dictionary: {e}") from e
        return v

    PATH_TO_THIRD_PARTY_SKILL_NAME: ClassVar[dict[str, str]] = {
        ".cursorrules": "cursorrules",
        "agents.md": "agents",
        "agent.md": "agents",
        "claude.md": "claude",
        "gemini.md": "gemini",
    }

    @classmethod
    def load(
        cls,
        path: str | Path,
        skill_base_dir: Path | None = None,
    ) -> "Skill":
        """Load a skill from a markdown file with frontmatter.

        The agent's name is derived from its path relative to skill_base_dir,
        or from the directory name for AgentSkills-style SKILL.md files.

        Supports both OpenHands-specific frontmatter fields and AgentSkills
        standard fields (https://agentskills.io/specification).

        Args:
            path: Path to the skill file.
            skill_base_dir: Base directory for skills (used to derive relative names).
        """
        path = Path(path) if isinstance(path, str) else path

        with open(path) as f:
            file_content = f.read()

        if path.name.lower() == "skill.md":
            return cls._load_agentskills_skill(path, file_content)
        else:
            return cls._load_legacy_openhands_skill(path, file_content, skill_base_dir)

    @classmethod
    def _load_agentskills_skill(cls, path: Path, file_content: str) -> "Skill":
        """Load a skill from an AgentSkills-format SKILL.md file.

        Args:
            path: Path to the SKILL.md file.
            file_content: Content of the file.
        """
        # For SKILL.md files, use parent directory name as the skill name
        directory_name = path.parent.name

        file_io = io.StringIO(file_content)
        loaded = frontmatter.load(file_io)
        content = loaded.content
        metadata_dict = loaded.metadata or {}

        # Use name from frontmatter if provided, otherwise use directory name
        agent_name = str(metadata_dict.get("name", directory_name))

        # Validate skill name
        name_errors = _validate_skill_name(agent_name, directory_name)
        if name_errors:
            raise SkillValidationError(
                f"Invalid skill name '{agent_name}': {'; '.join(name_errors)}"
            )

        return cls._create_skill_from_metadata(agent_name, content, path, metadata_dict)

    @classmethod
    def _load_legacy_openhands_skill(
        cls, path: Path, file_content: str, skill_base_dir: Path | None
    ) -> "Skill":
        """Load a skill from a legacy OpenHands-format file.

        Args:
            path: Path to the skill file.
            file_content: Content of the file.
            skill_base_dir: Base directory for skills (used to derive relative names).
        """
        # Handle third-party agent instruction files
        third_party_agent = cls._handle_third_party(path, file_content)
        if third_party_agent is not None:
            return third_party_agent

        # Calculate derived name from path
        if skill_base_dir is not None:
            skill_name = cls.PATH_TO_THIRD_PARTY_SKILL_NAME.get(
                path.name.lower()
            ) or str(path.relative_to(skill_base_dir).with_suffix(""))
        else:
            skill_name = path.stem

        file_io = io.StringIO(file_content)
        loaded = frontmatter.load(file_io)
        content = loaded.content
        metadata_dict = loaded.metadata or {}

        # Use name from frontmatter if provided, otherwise use derived name
        agent_name = str(metadata_dict.get("name", skill_name))

        return cls._create_skill_from_metadata(agent_name, content, path, metadata_dict)

    @classmethod
    def _create_skill_from_metadata(
        cls, agent_name: str, content: str, path: Path, metadata_dict: dict
    ) -> "Skill":
        """Create a Skill object from parsed metadata.

        Args:
            agent_name: The name of the skill.
            content: The markdown content (without frontmatter).
            path: Path to the skill file.
            metadata_dict: Parsed frontmatter metadata.
        """
        # Extract AgentSkills standard fields (Pydantic validators handle
        # transformation). Handle "allowed-tools" to "allowed_tools" key mapping.
        allowed_tools_value = metadata_dict.get(
            "allowed-tools", metadata_dict.get("allowed_tools")
        )
        agentskills_fields = {
            "description": metadata_dict.get("description"),
            "license": metadata_dict.get("license"),
            "compatibility": metadata_dict.get("compatibility"),
            "metadata": metadata_dict.get("metadata"),
            "allowed_tools": allowed_tools_value,
        }
        # Remove None values to avoid passing unnecessary kwargs
        agentskills_fields = {
            k: v for k, v in agentskills_fields.items() if v is not None
        }

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
            if mcp_tools is not None and not isinstance(mcp_tools, dict):
                raise SkillValidationError("mcp_tools must be a dictionary or None")
            return Skill(
                name=agent_name,
                content=content,
                source=str(path),
                trigger=None,
                mcp_tools=mcp_tools,
                **agentskills_fields,
            )

    @classmethod
    def _handle_third_party(cls, path: Path, file_content: str) -> Union["Skill", None]:
        """Handle third-party skill files (e.g., .cursorrules, AGENTS.md).

        Creates a Skill with None trigger (always active) if the file type
        is recognized. Truncates content if it exceeds the limit.
        """
        skill_name = cls.PATH_TO_THIRD_PARTY_SKILL_NAME.get(path.name.lower())

        if skill_name is not None:
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


def _find_skill_md(skill_dir: Path) -> Path | None:
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


def _validate_skill_name(name: str, directory_name: str | None = None) -> list[str]:
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


def _find_third_party_files(repo_root: Path) -> list[Path]:
    """Find third-party skill files in the repository root.

    Searches for files like .cursorrules, AGENTS.md, CLAUDE.md, etc.
    with case-insensitive matching.

    Args:
        repo_root: Path to the repository root directory.

    Returns:
        List of paths to third-party skill files found.
    """
    if not repo_root.exists():
        return []

    # Build a set of target filenames (lowercase) for case-insensitive matching
    target_names = {name.lower() for name in Skill.PATH_TO_THIRD_PARTY_SKILL_NAME}

    files: list[Path] = []
    seen_names: set[str] = set()
    for item in repo_root.iterdir():
        if item.is_file() and item.name.lower() in target_names:
            # Avoid duplicates (e.g., AGENTS.md and agents.md in same dir)
            name_lower = item.name.lower()
            if name_lower in seen_names:
                logger.warning(
                    f"Duplicate third-party skill file ignored: {item} "
                    f"(already found a file with name '{name_lower}')"
                )
            else:
                files.append(item)
                seen_names.add(name_lower)
    return files


def _find_skill_md_directories(skill_dir: Path) -> list[Path]:
    """Find AgentSkills-style directories containing SKILL.md files.

    Args:
        skill_dir: Path to the skills directory.

    Returns:
        List of paths to SKILL.md files.
    """
    results: list[Path] = []
    if not skill_dir.exists():
        return results
    for subdir in skill_dir.iterdir():
        if subdir.is_dir():
            skill_md = _find_skill_md(subdir)
            if skill_md:
                results.append(skill_md)
    return results


def _find_regular_md_files(skill_dir: Path, exclude_dirs: set[Path]) -> list[Path]:
    """Find regular .md skill files, excluding SKILL.md and files in excluded dirs.

    Args:
        skill_dir: Path to the skills directory.
        exclude_dirs: Set of directories to exclude (e.g., SKILL.md directories).

    Returns:
        List of paths to regular .md skill files.
    """
    files: list[Path] = []
    if not skill_dir.exists():
        return files
    for f in skill_dir.rglob("*.md"):
        is_readme = f.name == "README.md"
        is_skill_md = f.name.lower() == "skill.md"
        is_in_excluded_dir = any(f.is_relative_to(d) for d in exclude_dirs)
        if not is_readme and not is_skill_md and not is_in_excluded_dir:
            files.append(f)
    return files


def _load_and_categorize(
    path: Path,
    skill_base_dir: Path,
    repo_skills: dict[str, Skill],
    knowledge_skills: dict[str, Skill],
    agent_skills: dict[str, Skill],
) -> None:
    """Load a skill and categorize it.

    Categorizes into repo_skills, knowledge_skills, or agent_skills.

    Args:
        path: Path to the skill file.
        skill_base_dir: Base directory for skills (used to derive relative names).
        repo_skills: Dictionary for skills with trigger=None (permanent context).
        knowledge_skills: Dictionary for skills with triggers (progressive).
        agent_skills: Dictionary for AgentSkills standard SKILL.md files.
    """
    skill = Skill.load(path, skill_base_dir)

    # AgentSkills (SKILL.md directories) are a separate category from OpenHands skills.
    # They follow the AgentSkills standard and should be handled differently.
    is_skill_md = path.name.lower() == "skill.md"
    if is_skill_md:
        agent_skills[skill.name] = skill
    elif skill.trigger is None:
        repo_skills[skill.name] = skill
    else:
        knowledge_skills[skill.name] = skill


def load_skills_from_dir(
    skill_dir: str | Path,
) -> tuple[dict[str, Skill], dict[str, Skill], dict[str, Skill]]:
    """Load all skills from the given directory.

    Supports both formats:
    - OpenHands format: skills/*.md files
    - AgentSkills format: skills/skill-name/SKILL.md directories

    Note, legacy repo instructions will not be loaded here.

    Args:
        skill_dir: Path to the skills directory (e.g. .openhands/skills)

    Returns:
        Tuple of (repo_skills, knowledge_skills, agent_skills) dictionaries.
        - repo_skills: Skills with trigger=None (permanent context)
        - knowledge_skills: Skills with KeywordTrigger or TaskTrigger (progressive)
        - agent_skills: AgentSkills standard SKILL.md files (separate category)
    """
    if isinstance(skill_dir, str):
        skill_dir = Path(skill_dir)

    repo_skills: dict[str, Skill] = {}
    knowledge_skills: dict[str, Skill] = {}
    agent_skills: dict[str, Skill] = {}
    logger.debug(f"Loading agents from {skill_dir}")

    # Discover all skill files
    repo_root = skill_dir.parent.parent
    third_party_files = _find_third_party_files(repo_root)
    skill_md_files = _find_skill_md_directories(skill_dir)
    skill_md_dirs = {skill_md.parent for skill_md in skill_md_files}
    regular_md_files = _find_regular_md_files(skill_dir, skill_md_dirs)

    # Load third-party files
    for path in third_party_files:
        _load_and_categorize(
            path, skill_dir, repo_skills, knowledge_skills, agent_skills
        )

    # Load SKILL.md files (auto-detected and validated in Skill.load)
    for skill_md_path in skill_md_files:
        _load_and_categorize(
            skill_md_path, skill_dir, repo_skills, knowledge_skills, agent_skills
        )

    # Load regular .md files
    for path in regular_md_files:
        _load_and_categorize(
            path, skill_dir, repo_skills, knowledge_skills, agent_skills
        )

    total = len(repo_skills) + len(knowledge_skills) + len(agent_skills)
    logger.debug(
        f"Loaded {total} skills: "
        f"repo={list(repo_skills.keys())}, "
        f"knowledge={list(knowledge_skills.keys())}, "
        f"agent={list(agent_skills.keys())}"
    )
    return repo_skills, knowledge_skills, agent_skills


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
            repo_skills, knowledge_skills, agent_skills = load_skills_from_dir(
                skills_dir
            )

            # Merge all skill categories
            for skills_dict in [repo_skills, knowledge_skills, agent_skills]:
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
            repo_skills, knowledge_skills, agent_skills = load_skills_from_dir(
                project_skills_dir
            )

            # Merge all skill categories
            for skills_dict in [repo_skills, knowledge_skills, agent_skills]:
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
                    skill_base_dir=repo_path,
                )
                if skill is None:
                    continue
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
