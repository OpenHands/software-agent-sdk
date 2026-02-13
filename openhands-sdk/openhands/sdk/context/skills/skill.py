import io
import re
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Union
from xml.sax.saxutils import escape as xml_escape

import frontmatter
from fastmcp.mcp_config import MCPConfig
from pydantic import BaseModel, Field, field_validator, model_validator

from openhands.sdk.context.skills.exceptions import SkillError, SkillValidationError
from openhands.sdk.context.skills.trigger import (
    KeywordTrigger,
    TaskTrigger,
)
from openhands.sdk.context.skills.types import InputMetadata
from openhands.sdk.context.skills.utils import (
    discover_skill_resources,
    find_mcp_config,
    find_regular_md_files,
    find_skill_md_directories,
    find_third_party_files,
    get_skills_cache_dir,
    load_and_categorize,
    load_mcp_config,
    update_skills_repository,
    validate_skill_name,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class SkillInfo(BaseModel):
    """Lightweight representation of a skill's essential information.

    This class provides a standardized, serializable format for skill metadata
    that can be used across different components of the system.
    """

    name: str
    type: Literal["repo", "knowledge", "agentskills"]
    content: str
    triggers: list[str] = Field(default_factory=list)
    source: str | None = None
    description: str | None = None
    is_agentskills_format: bool = False


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


# Union type for all trigger types
TriggerType = Annotated[
    KeywordTrigger | TaskTrigger,
    Field(discriminator="type"),
]


class Skill(BaseModel):
    """A skill provides specialized knowledge or functionality.

    Skill behavior depends on format (is_agentskills_format) and trigger:

    AgentSkills format (SKILL.md files):
    - Always listed in <available_skills> with name, description, location
    - Agent reads full content on demand (progressive disclosure)
    - If has triggers: content is ALSO auto-injected when triggered

    Legacy OpenHands format:
    - With triggers: Listed in <available_skills>, content injected on trigger
    - Without triggers (None): Full content in <REPO_CONTEXT>, always active

    This model supports both OpenHands-specific fields and AgentSkills standard
    fields (https://agentskills.io/specification) for cross-platform compatibility.
    """

    name: str
    content: str
    trigger: TriggerType | None = Field(
        default=None,
        description=(
            "Trigger determines when skill content is auto-injected. "
            "None = no auto-injection (for AgentSkills: agent reads on demand; "
            "for legacy: full content always in system prompt). "
            "KeywordTrigger = auto-inject when keywords appear in user messages. "
            "TaskTrigger = auto-inject for specific tasks, may require user input."
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
    is_agentskills_format: bool = Field(
        default=False,
        description=(
            "Whether this skill was loaded from a SKILL.md file following the "
            "AgentSkills standard. AgentSkills-format skills use progressive "
            "disclosure: always listed in <available_skills> with name, "
            "description, and location. If the skill also has triggers, content "
            "is auto-injected when triggered AND agent can read file anytime."
        ),
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
    resources: SkillResources | None = Field(
        default=None,
        description=(
            "Resource directories for the skill (scripts/, references/, assets/). "
            "AgentSkills standard field. Only populated for SKILL.md directory format."
        ),
    )

    @field_validator("description")
    @classmethod
    def _validate_description_length(cls, v: str | None) -> str | None:
        """Validate description length per AgentSkills spec (max 1024 chars)."""
        if v is not None and len(v) > 1024:
            raise SkillValidationError(
                f"Description exceeds 1024 characters ({len(v)} chars)"
            )
        return v

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
        strict: bool = True,
    ) -> "Skill":
        """Load a skill from a markdown file with frontmatter.

        The agent's name is derived from its path relative to skill_base_dir,
        or from the directory name for AgentSkills-style SKILL.md files.

        Supports both OpenHands-specific frontmatter fields and AgentSkills
        standard fields (https://agentskills.io/specification).

        Args:
            path: Path to the skill file.
            skill_base_dir: Base directory for skills (used to derive relative names).
            strict: If True, enforce strict AgentSkills name validation.
                If False, allow relaxed naming (e.g., for plugin compatibility).
        """
        path = Path(path) if isinstance(path, str) else path

        with open(path) as f:
            file_content = f.read()

        if path.name.lower() == "skill.md":
            return cls._load_agentskills_skill(path, file_content, strict=strict)
        else:
            return cls._load_legacy_openhands_skill(path, file_content, skill_base_dir)

    @classmethod
    def _load_agentskills_skill(
        cls, path: Path, file_content: str, strict: bool = True
    ) -> "Skill":
        """Load a skill from an AgentSkills-format SKILL.md file.

        Args:
            path: Path to the SKILL.md file.
            file_content: Content of the file.
            strict: If True, enforce strict AgentSkills name validation.
        """
        # For SKILL.md files, use parent directory name as the skill name
        directory_name = path.parent.name
        skill_root = path.parent

        file_io = io.StringIO(file_content)
        loaded = frontmatter.load(file_io)
        content = loaded.content
        metadata_dict = loaded.metadata or {}

        # Use name from frontmatter if provided, otherwise use directory name
        agent_name = str(metadata_dict.get("name", directory_name))

        # Validate skill name (only in strict mode)
        if strict:
            name_errors = validate_skill_name(agent_name, directory_name)
            if name_errors:
                raise SkillValidationError(
                    f"Invalid skill name '{agent_name}': {'; '.join(name_errors)}"
                )

        # Load MCP configuration from .mcp.json (agent_skills ONLY use .mcp.json)
        mcp_tools: dict | None = None
        mcp_json_path = find_mcp_config(skill_root)
        if mcp_json_path:
            mcp_tools = load_mcp_config(mcp_json_path, skill_root)

        # Discover resource directories
        resources: SkillResources | None = None
        discovered_resources = discover_skill_resources(skill_root)
        if discovered_resources.has_resources():
            resources = discovered_resources

        return cls._create_skill_from_metadata(
            agent_name,
            content,
            path,
            metadata_dict,
            mcp_tools,
            resources=resources,
            is_agentskills_format=True,
        )

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

        # Legacy skills ONLY use mcp_tools from frontmatter (not .mcp.json)
        mcp_tools = metadata_dict.get("mcp_tools")
        if mcp_tools is not None and not isinstance(mcp_tools, dict):
            raise SkillValidationError("mcp_tools must be a dictionary or None")

        return cls._create_skill_from_metadata(
            agent_name, content, path, metadata_dict, mcp_tools
        )

    @classmethod
    def _create_skill_from_metadata(
        cls,
        agent_name: str,
        content: str,
        path: Path,
        metadata_dict: dict,
        mcp_tools: dict | None = None,
        resources: SkillResources | None = None,
        is_agentskills_format: bool = False,
    ) -> "Skill":
        """Create a Skill object from parsed metadata.

        Args:
            agent_name: The name of the skill.
            content: The markdown content (without frontmatter).
            path: Path to the skill file.
            metadata_dict: Parsed frontmatter metadata.
            mcp_tools: MCP tools configuration (from .mcp.json or frontmatter).
            resources: Discovered resource directories.
            is_agentskills_format: Whether this skill follows the AgentSkills standard.
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
                mcp_tools=mcp_tools,
                resources=resources,
                is_agentskills_format=is_agentskills_format,
                **agentskills_fields,
            )

        elif metadata_dict.get("triggers", None):
            return Skill(
                name=agent_name,
                content=content,
                source=str(path),
                trigger=KeywordTrigger(keywords=keywords),
                mcp_tools=mcp_tools,
                resources=resources,
                is_agentskills_format=is_agentskills_format,
                **agentskills_fields,
            )
        else:
            # No triggers, default to None (always active)
            return Skill(
                name=agent_name,
                content=content,
                source=str(path),
                trigger=None,
                mcp_tools=mcp_tools,
                resources=resources,
                is_agentskills_format=is_agentskills_format,
                **agentskills_fields,
            )

    @classmethod
    def _handle_third_party(cls, path: Path, file_content: str) -> Union["Skill", None]:
        """Handle third-party skill files (e.g., .cursorrules, AGENTS.md).

        Creates a Skill with None trigger (always active) if the file type
        is recognized.
        """
        skill_name = cls.PATH_TO_THIRD_PARTY_SKILL_NAME.get(path.name.lower())

        if skill_name is not None:
            return Skill(
                name=skill_name,
                content=file_content,
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

    def get_skill_type(self) -> Literal["repo", "knowledge", "agentskills"]:
        """Determine the type of this skill.

        Returns:
            "agentskills" for AgentSkills format, "repo" for always-active skills,
            "knowledge" for trigger-based skills.
        """
        if self.is_agentskills_format:
            return "agentskills"
        elif self.trigger is None:
            return "repo"
        else:
            return "knowledge"

    def get_triggers(self) -> list[str]:
        """Extract trigger keywords from this skill.

        Returns:
            List of trigger strings, or empty list if no triggers.
        """
        if isinstance(self.trigger, KeywordTrigger):
            return self.trigger.keywords
        elif isinstance(self.trigger, TaskTrigger):
            return self.trigger.triggers
        return []

    def to_skill_info(self) -> SkillInfo:
        """Convert this skill to a SkillInfo.

        Returns:
            SkillInfo containing the skill's essential information.
        """
        return SkillInfo(
            name=self.name,
            type=self.get_skill_type(),
            content=self.content,
            triggers=self.get_triggers(),
            source=self.source,
            description=self.description,
            is_agentskills_format=self.is_agentskills_format,
        )


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

    # Discover skill files in the skills directory
    # Note: Third-party files (AGENTS.md, etc.) are loaded separately by
    # load_project_skills() to ensure they're loaded even when this directory
    # doesn't exist.
    skill_md_files = find_skill_md_directories(skill_dir)
    skill_md_dirs = {skill_md.parent for skill_md in skill_md_files}
    regular_md_files = find_regular_md_files(skill_dir, skill_md_dirs)

    # Load SKILL.md files (auto-detected and validated in Skill.load)
    for skill_md_path in skill_md_files:
        load_and_categorize(
            skill_md_path, skill_dir, repo_skills, knowledge_skills, agent_skills
        )

    # Load regular .md files
    for path in regular_md_files:
        load_and_categorize(
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

    Searches for skills in {work_dir}/.agents/skills/,
    {work_dir}/.openhands/skills/, and {work_dir}/.openhands/microagents/
    (legacy). Skills are merged in priority order, with earlier directories
    taking precedence for duplicate names.

    Use .agents/skills for new skills. .openhands/skills is the legacy
    OpenHands location, and .openhands/microagents is deprecated.

    Example: If "my-skill" exists in both .agents/skills/ and
    .openhands/skills/, the version from .agents/skills/ is used.

    Also loads third-party skill files (AGENTS.md, .cursorrules, etc.)
    directly from the work directory.

    Args:
        work_dir: Path to the project/working directory.

    Returns:
        List of Skill objects loaded from project directories.
        Returns empty list if no skills found or loading fails.
    """
    if isinstance(work_dir, str):
        work_dir = Path(work_dir)

    all_skills = []
    seen_names: set[str] = set()

    # First, load third-party skill files directly from work directory
    # This ensures they are loaded even if .openhands/skills doesn't exist
    third_party_files = find_third_party_files(
        work_dir, Skill.PATH_TO_THIRD_PARTY_SKILL_NAME
    )
    for path in third_party_files:
        try:
            skill = Skill.load(path)
            if skill.name not in seen_names:
                all_skills.append(skill)
                seen_names.add(skill.name)
                logger.debug(f"Loaded third-party skill: {skill.name} from {path}")
        except (SkillError, OSError) as e:
            logger.warning(f"Failed to load third-party skill from {path}: {e}")

    # Load project-specific skills from .agents/skills, .openhands/skills,
    # and legacy microagents (priority order; first wins for duplicates)
    project_skills_dirs = [
        work_dir / ".agents" / "skills",
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

            # Merge all skill categories (skip duplicates including third-party)
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

    Note: When a skill directory contains a SKILL.md file (AgentSkills format),
    any other markdown files in that directory or its subdirectories are treated
    as reference materials for that skill, NOT as separate skills.

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
        cache_dir = get_skills_cache_dir()
        repo_path = update_skills_repository(repo_url, branch, cache_dir)

        if repo_path is None:
            logger.warning("Failed to access public skills repository")
            return all_skills

        # Load skills from the local repository
        skills_dir = repo_path / "skills"
        if not skills_dir.exists():
            logger.warning(f"Skills directory not found in repository: {skills_dir}")
            return all_skills

        # Find SKILL.md directories (AgentSkills format) and regular .md files
        # This ensures that markdown files in SKILL.md directories are NOT loaded
        # as separate skills - they are reference materials for the parent skill.
        skill_md_files = find_skill_md_directories(skills_dir)
        skill_md_dirs = {skill_md.parent for skill_md in skill_md_files}
        regular_md_files = find_regular_md_files(skills_dir, skill_md_dirs)

        # Combine all skill files to load
        all_skill_files = list(skill_md_files) + list(regular_md_files)
        logger.info(
            f"Found {len(all_skill_files)} skill files in public skills repository"
        )

        # Load each skill file
        for skill_file in all_skill_files:
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


def to_prompt(skills: list[Skill], max_description_length: int = 200) -> str:
    """Generate XML prompt block for available skills.

    Creates an `<available_skills>` XML block suitable for inclusion
    in system prompts, following the AgentSkills format from skills-ref.

    Args:
        skills: List of skills to include in the prompt
        max_description_length: Maximum length for descriptions (default 200)

    Returns:
        XML string in AgentSkills format with name, description, and location

    Example:
        >>> skills = [Skill(name="pdf-tools", content="...",
        ...                 description="Extract text from PDF files.",
        ...                 source="/path/to/skill")]
        >>> print(to_prompt(skills))
        <available_skills>
          <skill>
            <name>pdf-tools</name>
            <description>Extract text from PDF files.</description>
            <location>/path/to/skill</location>
          </skill>
        </available_skills>
    """
    if not skills:
        return "<available_skills>\n  no available skills\n</available_skills>"

    lines = ["<available_skills>"]
    for skill in skills:
        # Use description if available, otherwise use first line of content
        description = skill.description
        content_truncated = 0
        if not description:
            # Extract first non-empty, non-header line from content as fallback
            # Track position to calculate truncated content after the description
            chars_before_desc = 0
            for line in skill.content.split("\n"):
                stripped = line.strip()
                # Skip markdown headers and empty lines
                if not stripped or stripped.startswith("#"):
                    chars_before_desc += len(line) + 1  # +1 for newline
                    continue
                description = stripped
                # Calculate remaining content after this line as truncated
                desc_end_pos = chars_before_desc + len(line)
                content_truncated = max(0, len(skill.content) - desc_end_pos)
                break
        description = description or ""

        # Calculate total truncated characters
        total_truncated = content_truncated

        # Truncate description if needed and add truncation indicator
        if len(description) > max_description_length:
            total_truncated += len(description) - max_description_length
            description = description[:max_description_length]

        if total_truncated > 0:
            truncation_msg = f"... [{total_truncated} characters truncated"
            if skill.source:
                truncation_msg += f". View {skill.source} for complete information"
            truncation_msg += "]"
            description = description + truncation_msg

        # Escape XML special characters using standard library
        description = xml_escape(description.strip())
        name = xml_escape(skill.name.strip())

        # Build skill element following AgentSkills format from skills-ref
        lines.append("  <skill>")
        lines.append(f"    <name>{name}</name>")
        lines.append(f"    <description>{description}</description>")
        if skill.source:
            source = xml_escape(skill.source.strip())
            lines.append(f"    <location>{source}</location>")
        lines.append("  </skill>")

    lines.append("</available_skills>")
    return "\n".join(lines)
