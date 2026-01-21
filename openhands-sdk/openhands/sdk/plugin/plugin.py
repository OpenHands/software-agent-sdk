"""Plugin class for loading and managing plugins."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openhands.sdk.context import AgentContext
from openhands.sdk.context.skills import Skill
from openhands.sdk.context.skills.utils import (
    discover_skill_resources,
    find_skill_md,
    load_mcp_config,
)
from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.plugin.fetch import fetch_plugin
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

    def add_skills_to(
        self,
        agent_context: AgentContext | None = None,
        max_skills: int | None = None,
    ) -> AgentContext:
        """Add this plugin's skills to an agent context.

        Plugin skills override existing skills with the same name.

        Args:
            agent_context: Existing agent context (or None to create new)
            max_skills: Optional max total skills (raises ValueError if exceeded)

        Returns:
            New AgentContext with this plugin's skills added

        Raises:
            ValueError: If max_skills limit would be exceeded

        Example:
            >>> plugin = Plugin.load(Plugin.fetch("github:owner/plugin"))
            >>> new_context = plugin.add_skills_to(agent.agent_context, max_skills=100)
            >>> agent = agent.model_copy(update={"agent_context": new_context})
        """
        existing_skills = agent_context.skills if agent_context else []

        skills_by_name = {s.name: s for s in existing_skills}
        for skill in self.skills:
            if skill.name in skills_by_name:
                logger.warning(f"Plugin skill '{skill.name}' overrides existing skill")
            skills_by_name[skill.name] = skill

        if max_skills is not None and len(skills_by_name) > max_skills:
            raise ValueError(
                f"Total skills ({len(skills_by_name)}) exceeds maximum ({max_skills})"
            )

        merged_skills = list(skills_by_name.values())

        if agent_context:
            return agent_context.model_copy(update={"skills": merged_skills})
        return AgentContext(skills=merged_skills)

    def add_mcp_config_to(
        self,
        mcp_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add this plugin's MCP servers to an MCP config.

        Plugin MCP servers override existing servers with the same name.

        Merge semantics (Claude Code compatible):
        - mcpServers: deep-merge by server name (last plugin wins for same server)
        - Other top-level keys: shallow override (plugin wins)

        Args:
            mcp_config: Existing MCP config (or None to create new)

        Returns:
            New MCP config dict with this plugin's servers added

        Example:
            >>> plugin = Plugin.load(Plugin.fetch("github:owner/plugin"))
            >>> new_mcp = plugin.add_mcp_config_to(agent.mcp_config)
            >>> agent = agent.model_copy(update={"mcp_config": new_mcp})
        """
        base_config = mcp_config
        plugin_config = self.mcp_config

        if base_config is None and plugin_config is None:
            return {}
        if base_config is None:
            return dict(plugin_config) if plugin_config else {}
        if plugin_config is None:
            return dict(base_config)

        # Shallow copy to avoid mutating inputs
        result = dict(base_config)

        # Merge mcpServers by server name (Claude Code compatible behavior)
        if "mcpServers" in plugin_config:
            existing_servers = result.get("mcpServers", {})
            for server_name in plugin_config["mcpServers"]:
                if server_name in existing_servers:
                    logger.warning(
                        f"Plugin MCP server '{server_name}' overrides existing server"
                    )
            result["mcpServers"] = {
                **existing_servers,
                **plugin_config["mcpServers"],
            }

        # Other top-level keys: plugin wins (shallow override)
        for key, value in plugin_config.items():
            if key != "mcpServers":
                if key in result:
                    logger.warning(
                        f"Plugin MCP config key '{key}' overrides existing value"
                    )
                result[key] = value

        return result

    @classmethod
    def fetch(
        cls,
        source: str,
        cache_dir: Path | None = None,
        ref: str | None = None,
        update: bool = True,
        repo_path: str | None = None,
    ) -> Path:
        """Fetch a plugin from a remote source and return the local cached path.

        This method fetches plugins from remote sources (GitHub repositories, git URLs)
        and caches them locally. Use the returned path with Plugin.load() to load
        the plugin.

        Args:
            source: Plugin source - can be:
                - Any git URL (GitHub, GitLab, Bitbucket, Codeberg, self-hosted, etc.)
                  e.g., "https://gitlab.com/org/repo", "git@bitbucket.org:team/repo.git"
                - "github:owner/repo" - GitHub shorthand (convenience syntax)
                - "/local/path" - Local path (returned as-is)
            cache_dir: Directory for caching. Defaults to ~/.openhands/cache/plugins/
            ref: Optional branch, tag, or commit to checkout.
            update: If True and cache exists, update it. If False, use cached as-is.
            repo_path: Subdirectory path within the git repository
                (e.g., 'plugins/my-plugin' for monorepos). Only relevant for git
                sources, not local paths. If specified, the returned path will
                point to this subdirectory instead of the repository root.

        Returns:
            Path to the local plugin directory (ready for Plugin.load()).
            If repo_path is specified, returns the path to that subdirectory.

        Raises:
            PluginFetchError: If fetching fails or repo_path doesn't exist.

        Example:
            >>> path = Plugin.fetch("github:owner/my-plugin")
            >>> plugin = Plugin.load(path)

            >>> # With specific version
            >>> path = Plugin.fetch("github:owner/my-plugin", ref="v1.0.0")
            >>> plugin = Plugin.load(path)

            >>> # Fetch a plugin from a subdirectory in a monorepo
            >>> path = Plugin.fetch("github:owner/monorepo", repo_path="plugins/sub")
            >>> plugin = Plugin.load(path)

            >>> # Fetch and load in one step
            >>> plugin = Plugin.load(Plugin.fetch("github:owner/my-plugin"))
        """
        return fetch_plugin(
            source, cache_dir=cache_dir, ref=ref, update=update, repo_path=repo_path
        )

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

    Note: Plugin skills are loaded with relaxed validation (strict=False)
    to support Claude Code plugins which may use different naming conventions.
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
                    skill = Skill.load(skill_md, skills_dir, strict=False)
                    # Discover and attach resources
                    skill.resources = discover_skill_resources(item)
                    skills.append(skill)
                    logger.debug(f"Loaded skill: {skill.name} from {skill_md}")
                except Exception as e:
                    logger.warning(f"Failed to load skill from {item}: {e}")
        elif item.suffix == ".md" and item.name.lower() != "readme.md":
            # Also support single .md files in skills/ directory
            try:
                skill = Skill.load(item, skills_dir, strict=False)
                skills.append(skill)
                logger.debug(f"Loaded skill: {skill.name} from {item}")
            except Exception as e:
                logger.warning(f"Failed to load skill from {item}: {e}")

    return skills


def _load_hooks(plugin_dir: Path) -> HookConfig | None:
    """Load hooks configuration from hooks/hooks.json."""
    hooks_json = plugin_dir / "hooks" / "hooks.json"
    if not hooks_json.exists():
        return None

    try:
        hook_config = HookConfig.load(path=hooks_json)
        # If hooks.json exists but is invalid, HookConfig.load() returns an empty
        # config and logs the validation error. Keep that distinct from "file not
        # present" (None).
        if hook_config.is_empty():
            logger.info(f"No hooks configured in {hooks_json}")
            return HookConfig()
        return hook_config
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
                agent = AgentDefinition.load(item)
                agents.append(agent)
                logger.debug(f"Loaded agent: {agent.name} from {item}")
            except Exception as e:
                logger.warning(f"Failed to load agent from {item}: {e}")

    return agents


def _load_commands(plugin_dir: Path) -> list[CommandDefinition]:
    """Load command definitions from the commands/ directory."""
    commands_dir = plugin_dir / "commands"
    if not commands_dir.is_dir():
        return []

    commands: list[CommandDefinition] = []
    for item in commands_dir.iterdir():
        if item.suffix == ".md" and item.name.lower() != "readme.md":
            try:
                command = CommandDefinition.load(item)
                commands.append(command)
                logger.debug(f"Loaded command: {command.name} from {item}")
            except Exception as e:
                logger.warning(f"Failed to load command from {item}: {e}")

    return commands
