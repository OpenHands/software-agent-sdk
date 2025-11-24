import io
import re
import tempfile
from itertools import chain
from pathlib import Path
from typing import Annotated, ClassVar, Union

import frontmatter
import httpx
from fastmcp.mcp_config import MCPConfig
from pydantic import BaseModel, Field, field_validator, model_validator

from openhands.sdk.context.skills.exceptions import SkillValidationError
from openhands.sdk.context.skills.trigger import (
    KeywordTrigger,
    TaskTrigger,
)
from openhands.sdk.context.skills.types import InputMetadata
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

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

    PATH_TO_THIRD_PARTY_SKILL_NAME: ClassVar[dict[str, str]] = {
        ".cursorrules": "cursorrules",
        "agents.md": "agents",
        "agent.md": "agents",
    }

    @classmethod
    def _handle_third_party(cls, path: Path, file_content: str) -> Union["Skill", None]:
        # Determine the agent name based on file type
        skill_name = cls.PATH_TO_THIRD_PARTY_SKILL_NAME.get(path.name.lower())

        # Create Skill with None trigger (always active) if we recognized the file type
        if skill_name is not None:
            return Skill(
                name=skill_name,
                content=file_content,
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


# Public skills repository configuration
PUBLIC_SKILLS_REPO = "https://github.com/OpenHands/skills"
PUBLIC_SKILLS_BRANCH = "main"


def load_public_skills(
    repo_url: str = PUBLIC_SKILLS_REPO,
    branch: str = PUBLIC_SKILLS_BRANCH,
) -> list[Skill]:
    """Load skills from the public OpenHands skills repository.

    This function fetches skills from the public skills registry at
    https://github.com/OpenHands/skills. Skills are downloaded and loaded
    dynamically, allowing users to get the latest skills without SDK updates.

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

    # Extract owner and repo name from GitHub URL
    try:
        # Support both HTTPS and SSH URLs
        if repo_url.startswith("https://github.com/"):
            parts = repo_url.replace("https://github.com/", "").rstrip("/").split("/")
        elif repo_url.startswith("git@github.com:"):
            parts = repo_url.replace("git@github.com:", "").rstrip("/").split("/")
        else:
            logger.warning(
                f"Invalid repository URL format: {repo_url}. "
                f"Expected GitHub URL format."
            )
            return all_skills

        if len(parts) < 2:
            logger.warning(f"Invalid repository URL format: {repo_url}")
            return all_skills

        owner, repo = parts[0], parts[1].replace(".git", "")

        # Use GitHub API to list files in the skills/ subdirectory
        api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}:skills?recursive=1"

        logger.debug(f"Fetching skills from {repo_url} (branch: {branch})")

        with httpx.Client(timeout=30.0) as client:
            response = client.get(api_url)
            response.raise_for_status()
            tree_data = response.json()

            # Find all .md files in the skills/ directory
            md_files = [
                f"skills/{item['path']}"
                for item in tree_data.get("tree", [])
                if item["type"] == "blob"
                and item["path"].endswith(".md")
                and item["path"] != "README.md"
            ]

            logger.debug(f"Found {len(md_files)} skill files in repository")

            # Download and load each skill file
            for file_path in md_files:
                try:
                    # Use raw.githubusercontent.com for file content
                    raw_url = (
                        f"https://raw.githubusercontent.com/"
                        f"{owner}/{repo}/{branch}/{file_path}"
                    )
                    file_response = client.get(raw_url)
                    file_response.raise_for_status()
                    file_content = file_response.text

                    # Create a temporary directory structure for skill loading
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_path = Path(temp_dir)
                        skill_file = temp_path / file_path
                        skill_file.parent.mkdir(parents=True, exist_ok=True)
                        skill_file.write_text(file_content)

                        # Load the skill using existing load method
                        skill = Skill.load(
                            path=skill_file,
                            skill_dir=temp_path,
                            file_content=file_content,
                        )
                        all_skills.append(skill)
                        logger.debug(f"Loaded public skill: {skill.name}")

                except Exception as e:
                    logger.warning(f"Failed to load skill from {file_path}: {str(e)}")
                    continue

    except httpx.HTTPStatusError as e:
        logger.warning(
            f"HTTP error fetching skills from {repo_url}: "
            f"{e.response.status_code} - {e.response.text}"
        )
    except httpx.RequestError as e:
        logger.warning(f"Network error fetching skills from {repo_url}: {str(e)}")
    except Exception as e:
        logger.warning(f"Failed to load public skills from {repo_url}: {str(e)}")

    logger.debug(
        f"Loaded {len(all_skills)} public skills: {[s.name for s in all_skills]}"
    )
    return all_skills
