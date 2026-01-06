"""Plugin class for loading and managing plugins."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field

from openhands.sdk.context.skills import Skill
from openhands.sdk.context.skills.utils import (
    discover_skill_resources,
    find_skill_md,
    load_mcp_config,
)
from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.plugin.types import (
    AgentDefinition,
    CommandDefinition,
    PluginAuthor,
    PluginManifest,
)

logger = get_logger(__name__)

# Directories to check for plugin manifest
PLUGIN_MANIFEST_DIRS = [".plugin", ".claude-plugin"]
PLUGIN_MANIFEST_FILE = "plugin.json"


class Plugin(BaseModel):
    """A plugin that bundles skills, hooks, MCP config, agents, and commands.

    Plugins follow the Claude Code plugin structure for compatibility:

    ```
    plugin-name/
    ├── .claude-plugin/           # or .plugin/
    │   └── plugin.json          # Plugin metadata
    ├── commands/                # Slash commands (optional)
    ├── agents/                  # Specialized agents (optional)
    ├── skills/                  # Agent Skills (optional)
    ├── hooks/                   # Event handlers (optional)
    │   └── hooks.json
    ├── .mcp.json                # External tool configuration (optional)
    └── README.md                # Plugin documentation
    ```
    """

    manifest: PluginManifest = Field(description="Plugin manifest from plugin.json")
    path: str = Field(description="Path to the plugin directory")
    skills: list[Skill] = Field(
        default_factory=list, description="Skills loaded from skills/ directory"
    )
    hooks: HookConfig | None = Field(
        default=None, description="Hook configuration from hooks/hooks.json"
    )
    mcp_config: dict[str, Any] | None = Field(
        default=None, description="MCP configuration from .mcp.json"
    )
    agents: list[AgentDefinition] = Field(
        default_factory=list, description="Agent definitions from agents/ directory"
    )
    commands: list[CommandDefinition] = Field(
        default_factory=list, description="Command definitions from commands/ directory"
    )

    @property
    def name(self) -> str:
        """Get the plugin name."""
        return self.manifest.name

    @property
    def version(self) -> str:
        """Get the plugin version."""
        return self.manifest.version

    @property
    def description(self) -> str:
        """Get the plugin description."""
        return self.manifest.description

    @classmethod
    def load(cls, plugin_path: str | Path) -> Plugin:
        """Load a plugin from a directory.

        Args:
            plugin_path: Path to the plugin directory.

        Returns:
            Loaded Plugin instance.

        Raises:
            FileNotFoundError: If the plugin directory doesn't exist.
            ValueError: If the plugin manifest is invalid.
        """
        plugin_dir = Path(plugin_path).resolve()
        if not plugin_dir.is_dir():
            raise FileNotFoundError(f"Plugin directory not found: {plugin_dir}")

        # Load manifest
        manifest = _load_manifest(plugin_dir)

        # Load skills
        skills = _load_skills(plugin_dir)

        # Load hooks
        hooks = _load_hooks(plugin_dir)

        # Load MCP config
        mcp_config = _load_mcp_config(plugin_dir)

        # Load agents
        agents = _load_agents(plugin_dir)

        # Load commands
        commands = _load_commands(plugin_dir)

        return cls(
            manifest=manifest,
            path=str(plugin_dir),
            skills=skills,
            hooks=hooks,
            mcp_config=mcp_config,
            agents=agents,
            commands=commands,
        )

    @classmethod
    def load_all(cls, plugins_dir: str | Path) -> list[Plugin]:
        """Load all plugins from a directory.

        Args:
            plugins_dir: Path to directory containing plugin subdirectories.

        Returns:
            List of loaded Plugin instances.
        """
        plugins_path = Path(plugins_dir).resolve()
        if not plugins_path.is_dir():
            logger.warning(f"Plugins directory not found: {plugins_path}")
            return []

        plugins: list[Plugin] = []
        for item in plugins_path.iterdir():
            if item.is_dir():
                try:
                    plugin = cls.load(item)
                    plugins.append(plugin)
                    logger.debug(f"Loaded plugin: {plugin.name} from {item}")
                except Exception as e:
                    logger.warning(f"Failed to load plugin from {item}: {e}")

        return plugins


def _load_manifest(plugin_dir: Path) -> PluginManifest:
    """Load plugin manifest from plugin.json.

    Checks both .plugin/ and .claude-plugin/ directories.
    Falls back to inferring from directory name if no manifest found.
    """
    manifest_path = None

    # Check for manifest in standard locations
    for manifest_dir in PLUGIN_MANIFEST_DIRS:
        candidate = plugin_dir / manifest_dir / PLUGIN_MANIFEST_FILE
        if candidate.exists():
            manifest_path = candidate
            break

    if manifest_path:
        try:
            with open(manifest_path) as f:
                data = json.load(f)

            # Handle author field - can be string or object
            if "author" in data and isinstance(data["author"], str):
                data["author"] = PluginAuthor.from_string(data["author"]).model_dump()

            return PluginManifest.model_validate(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {manifest_path}: {e}") from e
        except Exception as e:
            raise ValueError(f"Failed to parse manifest {manifest_path}: {e}") from e

    # Fall back to inferring from directory name
    logger.debug(f"No manifest found for {plugin_dir}, inferring from directory name")
    return PluginManifest(
        name=plugin_dir.name,
        version="1.0.0",
        description=f"Plugin loaded from {plugin_dir.name}",
    )


def _load_skills(plugin_dir: Path) -> list[Skill]:
    """Load skills from the skills/ directory.

    Note: Plugin skills are loaded with relaxed validation compared to
    AgentSkills standard. This allows loading Claude Code plugins which
    may use different naming conventions.
    """
    skills_dir = plugin_dir / "skills"
    if not skills_dir.is_dir():
        return []

    skills: list[Skill] = []
    for item in skills_dir.iterdir():
        if item.is_dir():
            skill_md = find_skill_md(item)
            if skill_md:
                try:
                    skill = _load_skill_relaxed(skill_md, skills_dir, item)
                    skills.append(skill)
                    logger.debug(f"Loaded skill: {skill.name} from {skill_md}")
                except Exception as e:
                    logger.warning(f"Failed to load skill from {item}: {e}")
        elif item.suffix == ".md" and item.name.lower() != "readme.md":
            # Also support single .md files in skills/ directory
            try:
                skill = _load_skill_relaxed(item, skills_dir)
                skills.append(skill)
                logger.debug(f"Loaded skill: {skill.name} from {item}")
            except Exception as e:
                logger.warning(f"Failed to load skill from {item}: {e}")

    return skills


def _load_skill_relaxed(
    skill_path: Path, skills_dir: Path, skill_dir: Path | None = None
) -> Skill:
    """Load a skill with relaxed validation for plugin compatibility.

    This bypasses strict AgentSkills name validation to support
    Claude Code plugins which may use different naming conventions.
    """
    with open(skill_path) as f:
        post = frontmatter.load(f)

    fm = post.metadata
    content = post.content.strip()

    # Use name from frontmatter, or derive from path
    name = fm.get("name")
    if not name:
        if skill_path.name.lower() == "skill.md" and skill_path.parent != skills_dir:
            name = skill_path.parent.name
        else:
            name = skill_path.stem

    # Build skill with minimal validation
    skill = Skill(
        name=name,
        content=content,
        source=str(skill_path),
        trigger=None,  # Plugin skills are typically always-active
    )

    # Attach resources if skill_dir provided
    if skill_dir:
        skill.resources = discover_skill_resources(skill_dir)

    return skill


def _load_hooks(plugin_dir: Path) -> HookConfig | None:
    """Load hooks configuration from hooks/hooks.json."""
    hooks_json = plugin_dir / "hooks" / "hooks.json"
    if not hooks_json.exists():
        return None

    try:
        hook_config = HookConfig.load(path=hooks_json)
        # load() returns empty config on error, check if it has hooks
        if hook_config.hooks:
            return hook_config
        return None
    except Exception as e:
        logger.warning(f"Failed to load hooks from {hooks_json}: {e}")
        return None


def _load_mcp_config(plugin_dir: Path) -> dict[str, Any] | None:
    """Load MCP configuration from .mcp.json."""
    mcp_json = plugin_dir / ".mcp.json"
    if not mcp_json.exists():
        return None

    try:
        return load_mcp_config(mcp_json, skill_root=plugin_dir)
    except Exception as e:
        logger.warning(f"Failed to load MCP config from {mcp_json}: {e}")
        return None


def _load_agents(plugin_dir: Path) -> list[AgentDefinition]:
    """Load agent definitions from the agents/ directory."""
    agents_dir = plugin_dir / "agents"
    if not agents_dir.is_dir():
        return []

    agents: list[AgentDefinition] = []
    for item in agents_dir.iterdir():
        if item.suffix == ".md" and item.name.lower() != "readme.md":
            try:
                agent = _parse_agent_md(item)
                agents.append(agent)
                logger.debug(f"Loaded agent: {agent.name} from {item}")
            except Exception as e:
                logger.warning(f"Failed to load agent from {item}: {e}")

    return agents


def _parse_agent_md(agent_path: Path) -> AgentDefinition:
    """Parse an agent definition from a markdown file.

    Agent markdown files have YAML frontmatter with:
    - name: Agent name
    - description: Description with optional <example> tags for triggering
    - model: Model to use (default: 'inherit')
    - color: Display color
    - tools: List of allowed tools

    The body of the markdown is the system prompt.
    """
    try:
        with open(agent_path) as f:
            post = frontmatter.load(f)
        fm = post.metadata
        content = post.content.strip()
    except Exception:
        # If frontmatter parsing fails (e.g., due to colons in description),
        # try manual parsing
        fm, content = _parse_frontmatter_manual(agent_path)

    # Extract frontmatter fields
    name = fm.get("name", agent_path.stem)
    description = fm.get("description", "")
    model = fm.get("model", "inherit")
    color = fm.get("color")
    tools = fm.get("tools", [])

    # Ensure tools is a list
    if isinstance(tools, str):
        tools = [tools]

    # Extract whenToUse examples from description
    when_to_use_examples = _extract_examples(description)

    # Remove known fields from metadata to get extras
    known_fields = {"name", "description", "model", "color", "tools"}
    metadata = {k: v for k, v in fm.items() if k not in known_fields}

    return AgentDefinition(
        name=name,
        description=description,
        model=model,
        color=color,
        tools=tools,
        system_prompt=content,
        source=str(agent_path),
        when_to_use_examples=when_to_use_examples,
        metadata=metadata,
    )


def _parse_frontmatter_manual(file_path: Path) -> tuple[dict[str, Any], str]:
    """Manually parse frontmatter when standard parsing fails.

    This handles cases where YAML parsing fails due to special characters
    like colons in values that aren't properly quoted.
    """
    with open(file_path) as f:
        text = f.read()

    # Check for frontmatter delimiters
    if not text.startswith("---"):
        return {}, text

    # Find the end of frontmatter
    end_match = text.find("\n---", 3)
    if end_match == -1:
        return {}, text

    frontmatter_text = text[4:end_match]
    content = text[end_match + 4:].strip()

    # Parse frontmatter line by line
    fm: dict[str, Any] = {}
    current_key = None
    current_value_lines: list[str] = []

    for line in frontmatter_text.split("\n"):
        # Check if this is a new key-value pair
        if line and not line.startswith(" ") and not line.startswith("\t"):
            # Save previous key-value if exists
            if current_key:
                fm[current_key] = _parse_value("\n".join(current_value_lines))

            # Parse new key
            colon_idx = line.find(":")
            if colon_idx > 0:
                current_key = line[:colon_idx].strip()
                value_part = line[colon_idx + 1:].strip()
                current_value_lines = [value_part] if value_part else []
            else:
                current_key = None
                current_value_lines = []
        elif current_key:
            # Continuation of previous value
            current_value_lines.append(line)

    # Save last key-value
    if current_key:
        fm[current_key] = _parse_value("\n".join(current_value_lines))

    return fm, content


def _parse_value(value: str) -> Any:
    """Parse a YAML-like value string."""
    value = value.strip()

    # Handle arrays
    if value.startswith("[") and value.endswith("]"):
        # Simple array parsing
        inner = value[1:-1]
        items = []
        for item in inner.split(","):
            item = item.strip().strip('"').strip("'")
            if item:
                items.append(item)
        return items

    # Handle quoted strings
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]

    # Handle booleans
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False

    # Handle numbers
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass

    return value


def _extract_examples(description: str) -> list[str]:
    """Extract <example> tags from description for agent triggering."""
    pattern = r"<example>(.*?)</example>"
    matches = re.findall(pattern, description, re.DOTALL | re.IGNORECASE)
    return [m.strip() for m in matches if m.strip()]


def _load_commands(plugin_dir: Path) -> list[CommandDefinition]:
    """Load command definitions from the commands/ directory."""
    commands_dir = plugin_dir / "commands"
    if not commands_dir.is_dir():
        return []

    commands: list[CommandDefinition] = []
    for item in commands_dir.iterdir():
        if item.suffix == ".md" and item.name.lower() != "readme.md":
            try:
                command = _parse_command_md(item)
                commands.append(command)
                logger.debug(f"Loaded command: {command.name} from {item}")
            except Exception as e:
                logger.warning(f"Failed to load command from {item}: {e}")

    return commands


def _parse_command_md(command_path: Path) -> CommandDefinition:
    """Parse a command definition from a markdown file.

    Command markdown files have YAML frontmatter with:
    - description: Command description
    - argument-hint: Hint for command arguments (string or list)
    - allowed-tools: List of allowed tools

    The body of the markdown is the command instructions.
    """
    with open(command_path) as f:
        post = frontmatter.load(f)

    # Extract frontmatter fields
    fm = post.metadata
    name = command_path.stem  # Command name from filename
    description = fm.get("description", "")
    argument_hint = fm.get("argument-hint") or fm.get("argumentHint")
    allowed_tools = fm.get("allowed-tools") or fm.get("allowedTools") or []

    # Handle argument_hint as list (join with space)
    if isinstance(argument_hint, list):
        argument_hint = " ".join(str(h) for h in argument_hint)

    # Ensure allowed_tools is a list
    if isinstance(allowed_tools, str):
        allowed_tools = [allowed_tools]

    # Remove known fields from metadata to get extras
    known_fields = {"description", "argument-hint", "argumentHint", "allowed-tools", "allowedTools"}
    metadata = {k: v for k, v in fm.items() if k not in known_fields}

    return CommandDefinition(
        name=name,
        description=description,
        argument_hint=argument_hint,
        allowed_tools=allowed_tools,
        content=post.content.strip(),
        source=str(command_path),
        metadata=metadata,
    )
