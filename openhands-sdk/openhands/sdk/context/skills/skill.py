import io
import re
import shutil
import subprocess
from itertools import chain
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

# Standard resource directory names per AgentSkills spec
RESOURCE_DIRECTORIES = ("scripts", "references", "assets")


class SkillResources(BaseModel):
    """Resource directories for a skill (AgentSkills standard).

    Per the AgentSkills specification, skills can include:
    - scripts/: Executable scripts the agent can run
    - references/: Reference documentation and examples
    - assets/: Static assets (images, data files, etc.)
    """

    skill_root: str = Field(description="Root directory of the skill (absolute path)")
    scripts: list[str] = Field(
        default_factory=list,
        description="List of script files in scripts/ directory (relative paths)",
    )
    references: list[str] = Field(
        default_factory=list,
        description="List of reference files in references/ directory (relative paths)",
    )
    assets: list[str] = Field(
        default_factory=list,
        description="List of asset files in assets/ directory (relative paths)",
    )

    def has_resources(self) -> bool:
        """Check if any resources are available."""
        return bool(self.scripts or self.references or self.assets)

    def get_scripts_dir(self) -> Path | None:
        """Get the scripts directory path if it exists."""
        scripts_dir = Path(self.skill_root) / "scripts"
        return scripts_dir if scripts_dir.is_dir() else None

    def get_references_dir(self) -> Path | None:
        """Get the references directory path if it exists."""
        refs_dir = Path(self.skill_root) / "references"
        return refs_dir if refs_dir.is_dir() else None

    def get_assets_dir(self) -> Path | None:
        """Get the assets directory path if it exists."""
        assets_dir = Path(self.skill_root) / "assets"
        return assets_dir if assets_dir.is_dir() else None


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


def discover_skill_resources(skill_dir: Path) -> SkillResources:
    """Discover resource directories in a skill directory.

    Scans for standard AgentSkills resource directories:
    - scripts/: Executable scripts
    - references/: Reference documentation
    - assets/: Static assets

    Args:
        skill_dir: Path to the skill directory.

    Returns:
        SkillResources with lists of files in each resource directory.
    """
    resources = SkillResources(skill_root=str(skill_dir.resolve()))

    for resource_type in RESOURCE_DIRECTORIES:
        resource_dir = skill_dir / resource_type
        if resource_dir.is_dir():
            files = _list_resource_files(resource_dir, resource_type)
            setattr(resources, resource_type, files)

    return resources


def _list_resource_files(
    resource_dir: Path,
    resource_type: str,
) -> list[str]:
    """List files in a resource directory.

    Args:
        resource_dir: Path to the resource directory.
        resource_type: Type of resource (scripts, references, assets).

    Returns:
        List of relative file paths within the resource directory.
    """
    files: list[str] = []
    try:
        for item in resource_dir.rglob("*"):
            if item.is_file():
                # Store relative path from resource directory
                rel_path = item.relative_to(resource_dir)
                files.append(str(rel_path))
    except OSError as e:
        logger.warning(f"Error listing {resource_type} directory: {e}")
    return sorted(files)


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


def find_mcp_config(skill_dir: Path) -> Path | None:
    """Find .mcp.json file in a skill directory.

    Args:
        skill_dir: Path to the skill directory to search.

    Returns:
        Path to .mcp.json if found, None otherwise.
    """
    if not skill_dir.is_dir():
        return None
    mcp_json = skill_dir / ".mcp.json"
    if mcp_json.exists() and mcp_json.is_file():
        return mcp_json
    return None


def expand_mcp_variables(
    config: dict,
    variables: dict[str, str],
) -> dict:
    """Expand variables in MCP configuration.

    Supports variable expansion similar to Claude Code:
    - ${VAR} - Environment variables or provided variables
    - ${VAR:-default} - With default value

    Args:
        config: MCP configuration dictionary.
        variables: Dictionary of variable names to values.

    Returns:
        Configuration with variables expanded.
    """
    import json
    import os

    # Convert to JSON string for easy replacement
    config_str = json.dumps(config)

    # Pattern for ${VAR} or ${VAR:-default}
    var_pattern = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::-([^}]*))?\}")

    def replace_var(match: re.Match) -> str:
        var_name = match.group(1)
        default_value = match.group(2)

        # Check provided variables first, then environment
        if var_name in variables:
            return variables[var_name]
        if var_name in os.environ:
            return os.environ[var_name]
        if default_value is not None:
            return default_value
        # Return original if not found
        return match.group(0)

    config_str = var_pattern.sub(replace_var, config_str)
    return json.loads(config_str)


def load_mcp_config(
    mcp_json_path: Path,
    skill_root: Path | None = None,
) -> dict:
    """Load and parse .mcp.json with variable expansion.

    Args:
        mcp_json_path: Path to the .mcp.json file.
        skill_root: Root directory of the skill (for ${SKILL_ROOT} expansion).

    Returns:
        Parsed MCP configuration dictionary.

    Raises:
        SkillValidationError: If the file cannot be parsed or is invalid.
    """
    import json

    try:
        with open(mcp_json_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise SkillValidationError(f"Invalid JSON in {mcp_json_path}: {e}") from e
    except OSError as e:
        raise SkillValidationError(f"Cannot read {mcp_json_path}: {e}") from e

    if not isinstance(config, dict):
        raise SkillValidationError(
            f"Invalid .mcp.json format: expected object, got {type(config).__name__}"
        )

    # Prepare variables for expansion
    variables: dict[str, str] = {}
    if skill_root:
        variables["SKILL_ROOT"] = str(skill_root)

    # Expand variables
    config = expand_mcp_variables(config, variables)

    # Validate using MCPConfig
    try:
        MCPConfig.model_validate(config)
    except Exception as e:
        raise SkillValidationError(f"Invalid MCP configuration: {e}") from e

    return config


def validate_skill(skill_dir: str | Path) -> list[str]:
    """Validate a skill directory according to AgentSkills spec.

    Performs basic validation of skill structure and metadata:
    - Checks for SKILL.md file
    - Validates skill name format
    - Validates frontmatter structure

    Args:
        skill_dir: Path to the skill directory containing SKILL.md

    Returns:
        List of validation error messages (empty if valid)
    """
    skill_path = Path(skill_dir)
    errors: list[str] = []

    # Check directory exists
    if not skill_path.is_dir():
        errors.append(f"Skill directory does not exist: {skill_path}")
        return errors

    # Check for SKILL.md
    skill_md = find_skill_md(skill_path)
    if not skill_md:
        errors.append("Missing SKILL.md file")
        return errors

    # Validate skill name (directory name)
    dir_name = skill_path.name
    name_errors = validate_skill_name(dir_name, dir_name)
    errors.extend(name_errors)

    # Parse and validate frontmatter
    try:
        content = skill_md.read_text(encoding="utf-8")
        parsed = frontmatter.loads(content)
        metadata = dict(parsed.metadata)

        # Check for recommended fields
        if not parsed.content.strip():
            errors.append("SKILL.md has no content (body is empty)")

        # Validate description length if present
        description = metadata.get("description")
        if isinstance(description, str) and len(description) > 1024:
            errors.append(
                f"Description exceeds 1024 characters ({len(description)} chars)"
            )

        # Validate mcp_tools if present
        mcp_tools = metadata.get("mcp_tools")
        if mcp_tools is not None and not isinstance(mcp_tools, dict):
            errors.append("mcp_tools must be a dictionary")

        # Validate triggers if present
        triggers = metadata.get("triggers")
        if triggers is not None and not isinstance(triggers, list):
            errors.append("triggers must be a list")

        # Validate inputs if present
        inputs = metadata.get("inputs")
        if inputs is not None and not isinstance(inputs, list):
            errors.append("inputs must be a list")

    except Exception as e:
        errors.append(f"Failed to parse SKILL.md: {e}")

    # Check for .mcp.json validity if present
    mcp_json = find_mcp_config(skill_path)
    if mcp_json:
        try:
            load_mcp_config(mcp_json, skill_path)
        except SkillValidationError as e:
            errors.append(f"Invalid .mcp.json: {e}")

    return errors


def to_prompt(skills: list["Skill"], include_location: bool = True) -> str:
    """Generate XML prompt block for available skills.

    Creates an `<available_skills>` XML block suitable for inclusion
    in system prompts, following the AgentSkills format.

    Args:
        skills: List of skills to include in the prompt
        include_location: Whether to include the location element (for filesystem
            agents). Default True.

    Returns:
        XML string in AgentSkills format

    Example:
        >>> skills = [Skill(name="pdf-tools", content="...", description="...",
        ...                 source="/path/to/pdf-tools/SKILL.md")]
        >>> print(to_prompt(skills))
        <available_skills>
        <skill>
        <name>pdf-tools</name>
        <description>Extract text from PDF files.</description>
        <location>/path/to/pdf-tools/SKILL.md</location>
        </skill>
        </available_skills>
    """
    if not skills:
        return "<available_skills>\n</available_skills>"

    lines = ["<available_skills>"]
    for skill in skills:
        # Use description if available, otherwise extract from content
        description = skill.description
        if not description:
            # Extract first non-empty line from content as fallback
            for line in skill.content.split("\n"):
                line = line.strip()
                # Skip markdown headers and empty lines
                if line and not line.startswith("#"):
                    description = line[:200]  # Limit to 200 chars
                    break
        description = description or ""

        lines.append("<skill>")
        lines.append(f"<name>{_escape_xml(skill.name)}</name>")
        lines.append(f"<description>{_escape_xml(description)}</description>")

        # Include location if available and requested
        if include_location and skill.source:
            lines.append(f"<location>{_escape_xml(skill.source)}</location>")

        # Include resources if available
        if skill.resources and skill.resources.has_resources():
            lines.append("<resources>")
            if skill.resources.scripts:
                scripts_dir = f"{skill.resources.skill_root}/scripts"
                lines.append(f"<scripts_dir>{_escape_xml(scripts_dir)}</scripts_dir>")
            if skill.resources.references:
                refs_dir = f"{skill.resources.skill_root}/references"
                lines.append(
                    f"<references_dir>{_escape_xml(refs_dir)}</references_dir>"
                )
            if skill.resources.assets:
                assets_dir = f"{skill.resources.skill_root}/assets"
                lines.append(f"<assets_dir>{_escape_xml(assets_dir)}</assets_dir>")
            lines.append("</resources>")

        lines.append("</skill>")

    lines.append("</available_skills>")
    return "\n".join(lines)


def _escape_xml(text: str) -> str:
    """Escape XML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


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
    """

    name: str
    content: str
    description: str | None = Field(
        default=None,
        description=(
            "Short description of the skill (max 1024 chars). "
            "Used for progressive disclosure in available_skills listing."
        ),
    )
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
    resources: SkillResources | None = Field(
        default=None,
        description=(
            "Resource directories for the skill (scripts, references, assets). "
            "Populated when loading from filesystem."
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
    def load(
        cls,
        path: str | Path,
        skill_dir: Path | None = None,
        file_content: str | None = None,
    ) -> "Skill":
        """Load a skill from a markdown file with frontmatter.

        The agent's name is derived from its path relative to the skill_dir.
        """
        path = Path(path) if isinstance(path, str) else path

        # Calculate derived name from relative path if skill_dir is provided
        skill_name = None
        if skill_dir is not None:
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
            )

        elif metadata_dict.get("triggers", None):
            return Skill(
                name=agent_name,
                content=content,
                source=str(path),
                trigger=KeywordTrigger(keywords=keywords),
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
) -> tuple[dict[str, Skill], dict[str, Skill]]:
    """Load all skills from the given directory.

    Note, legacy repo instructions will not be loaded here.

    Args:
        skill_dir: Path to the skills directory (e.g. .openhands/skills)

    Returns:
        Tuple of (repo_skills, knowledge_skills) dictionaries.
        repo_skills have trigger=None, knowledge_skills have KeywordTrigger
        or TaskTrigger.
    """
    if isinstance(skill_dir, str):
        skill_dir = Path(skill_dir)

    repo_skills = {}
    knowledge_skills = {}

    # Load all agents from skills directory
    logger.debug(f"Loading agents from {skill_dir}")

    # Always check for .cursorrules and AGENTS.md files in repo root
    special_files = []
    repo_root = skill_dir.parent.parent

    # Check for third party rules: .cursorrules, AGENTS.md, etc
    for filename in Skill.PATH_TO_THIRD_PARTY_SKILL_NAME.keys():
        for variant in [filename, filename.lower(), filename.upper()]:
            if (repo_root / variant).exists():
                special_files.append(repo_root / variant)
                break  # Only add the first one found to avoid duplicates

    # Collect .md files from skills directory if it exists
    md_files = []
    if skill_dir.exists():
        md_files = [f for f in skill_dir.rglob("*.md") if f.name != "README.md"]

    # Process all files in one loop
    for file in chain(special_files, md_files):
        try:
            skill = Skill.load(file, skill_dir)
            if skill.trigger is None:
                repo_skills[skill.name] = skill
            else:
                # KeywordTrigger and TaskTrigger skills
                knowledge_skills[skill.name] = skill
        except SkillValidationError as e:
            # For validation errors, include the original exception
            error_msg = f"Error loading skill from {file}: {str(e)}"
            raise SkillValidationError(error_msg) from e
        except Exception as e:
            # For other errors, wrap in a ValueError with detailed message
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
