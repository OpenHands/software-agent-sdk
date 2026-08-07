"""Plugin format strategies.

A *plugin format* owns everything that is specific to how a plugin is laid out
on disk: where its manifest lives and how it is validated, which file its MCP
config is read from and how variables in it are expanded, and how its client
extensions (commands / agents / hooks) are located. Each format's job is to read
a plugin directory and produce a normalized :class:`~openhands.sdk.plugin.Plugin`.

Everything *downstream* of a loaded plugin (merging skills into an agent context,
merging MCP servers, concatenating hooks) is format-agnostic and lives on
``Plugin`` itself, so adding a new format never touches the merge/apply path.

Detection precedence (:func:`detect_format`): a root-level ``plugin.json`` selects
the Agent Plugins layout; otherwise the Claude Code layout is used (this is also
the fallback when no manifest is present at all). Only the Claude Code format is
implemented today; the Agent Plugins format (agent-plugins.org) is a planned
follow-up and is why this seam exists.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.plugin.types import (
    CommandDefinition,
    PluginAuthor,
    PluginManifest,
)
from openhands.sdk.skills.skill import Skill
from openhands.sdk.skills.utils import find_skill_md, load_mcp_config
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.utils.path import to_posix_path


if TYPE_CHECKING:
    from openhands.sdk.plugin.plugin import Plugin

logger = get_logger(__name__)

# Root-level manifest file name used by the Agent Plugins layout. Its presence is
# the signal used by detect_format() to pick that format over Claude Code.
ROOT_MANIFEST_FILE = "plugin.json"

# Directories the Claude Code layout checks for a (nested) manifest, in order.
PLUGIN_MANIFEST_DIRS = [".plugin", ".claude-plugin"]
PLUGIN_MANIFEST_FILE = "plugin.json"


class PluginFormat(ABC):
    """Strategy that reads a plugin directory into a normalized ``Plugin``.

    Subclasses implement the format-specific pieces (manifest, MCP config, and
    client extensions). Skills discovery and the final assembly in :meth:`load`
    are shared, because the skills rule is identical across formats and the
    output model is format-neutral.
    """

    #: Stable identifier used in logs and by ``detect_format``.
    name: ClassVar[str]

    @classmethod
    @abstractmethod
    def detect(cls, plugin_dir: Path) -> bool:
        """Return True if ``plugin_dir`` should be loaded with this format."""

    @abstractmethod
    def load_manifest(self, plugin_dir: Path) -> PluginManifest:
        """Load and validate the plugin manifest."""

    @abstractmethod
    def load_mcp_config(self, plugin_dir: Path) -> dict[str, MCPServer]:
        """Load the plugin's MCP server configuration."""

    @abstractmethod
    def load_hooks(self, plugin_dir: Path) -> HookConfig | None:
        """Load the plugin's hook configuration (or None if absent)."""

    @abstractmethod
    def load_agents(self, plugin_dir: Path) -> list[AgentDefinition]:
        """Load the plugin's agent definitions."""

    @abstractmethod
    def load_commands(self, plugin_dir: Path) -> list[CommandDefinition]:
        """Load the plugin's command definitions."""

    def load_skills(self, plugin_dir: Path) -> list[Skill]:
        """Discover a plugin's skills.

        Shared across formats: the ``skills/<name>/SKILL.md`` discovery rule is
        identical for Claude Code and Agent Plugins. Supports two layouts:

        - Multi-skill: a ``skills/`` directory containing one ``<name>/SKILL.md``
          per skill (or single ``.md`` files).
        - Single-skill: a ``SKILL.md`` at the plugin root when there is no
          ``skills/`` directory. Claude Code loads such a plugin as a single-skill
          plugin (v2.1.142+); this mirrors that behavior so standalone Agent Skills
          published as plugins load without an extra nesting level.

        Note: Plugin skills are loaded with relaxed validation (strict=False)
        to support Claude Code plugins which may use different naming conventions.
        """
        skills_dir = plugin_dir / "skills"
        if skills_dir.is_dir():
            return _load_skills_from_skills_dir(skills_dir)

        root_skill_md = find_skill_md(plugin_dir)
        if root_skill_md is not None:
            return _load_root_skill(plugin_dir, root_skill_md)

        return []

    def load(self, plugin_dir: Path) -> Plugin:
        """Assemble a normalized ``Plugin`` from ``plugin_dir``.

        This orchestration is shared: every format produces the same in-memory
        model, so downstream merge/apply logic never needs to know the format.
        """
        # Imported lazily to avoid a module-level import cycle with plugin.py,
        # which imports this module for detect_format().
        from openhands.sdk.plugin.plugin import Plugin

        manifest = self.load_manifest(plugin_dir)
        skills = self.load_skills(plugin_dir)
        hooks = self.load_hooks(plugin_dir)
        mcp_config = self.load_mcp_config(plugin_dir)
        agents = self.load_agents(plugin_dir)
        commands = self.load_commands(plugin_dir)

        return Plugin(
            manifest=manifest,
            path=to_posix_path(plugin_dir),
            skills=skills,
            hooks=hooks,
            mcp_config=mcp_config,
            agents=agents,
            commands=commands,
        )


class ClaudeCodePluginFormat(PluginFormat):
    """The Claude Code plugin layout (OpenHands' original format).

    ```
    plugin-name/
    ├── .claude-plugin/           # or .plugin/
    │   └── plugin.json          # Plugin metadata (nested)
    ├── commands/                # Slash commands (optional)
    ├── agents/                  # Specialized agents (optional)
    ├── skills/                  # Agent Skills (optional)
    ├── hooks/                   # Event handlers (optional)
    │   └── hooks.json
    ├── .mcp.json                # External tool configuration (optional)
    └── README.md                # Plugin documentation
    ```
    """

    name: ClassVar[str] = "claude-code"

    @classmethod
    def detect(cls, plugin_dir: Path) -> bool:  # noqa: ARG003
        # The Claude Code format is the fallback: it accepts any directory,
        # inferring a manifest from the directory name when none is present.
        return True

    def load_manifest(self, plugin_dir: Path) -> PluginManifest:
        """Load plugin manifest from ``plugin.json``.

        Checks both ``.plugin/`` and ``.claude-plugin/`` directories.
        Falls back to inferring from directory name if no manifest found.
        """
        manifest_path = None

        for manifest_dir in PLUGIN_MANIFEST_DIRS:
            candidate = plugin_dir / manifest_dir / PLUGIN_MANIFEST_FILE
            if candidate.exists():
                manifest_path = candidate
                break

        if manifest_path:
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    data = json.load(f)

                # Handle author field - can be string or object
                if "author" in data and isinstance(data["author"], str):
                    data["author"] = PluginAuthor.from_string(
                        data["author"]
                    ).model_dump()

                return PluginManifest.model_validate(data)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in {manifest_path}: {e}") from e
            except Exception as e:
                raise ValueError(
                    f"Failed to parse manifest {manifest_path}: {e}"
                ) from e

        # Fall back to inferring from directory name
        logger.debug(
            f"No manifest found for {plugin_dir}, inferring from directory name"
        )
        return PluginManifest(
            name=plugin_dir.name,
            version="1.0.0",
            description=f"Plugin loaded from {plugin_dir.name}",
        )

    def load_mcp_config(self, plugin_dir: Path) -> dict[str, MCPServer]:
        """Load MCP config from ``.mcp.json``.

        Note: Variables are NOT fully expanded during plugin loading. Only
        SKILL_ROOT is expanded (since plugin_dir is known). Other variables like
        ${VAR:-default} are preserved as placeholders to be expanded later when
        per-conversation secrets are available (in
        LocalConversation._ensure_plugins_loaded()).

        This prevents the double-expansion bug where defaults would be applied
        during plugin loading before secrets are available.
        """
        mcp_json = plugin_dir / ".mcp.json"
        if not mcp_json.exists():
            return {}

        try:
            # expand_defaults=False: preserve ${VAR:-default} placeholders for
            # later expansion with per-conversation secrets. Only SKILL_ROOT is
            # expanded now.
            config = load_mcp_config(
                mcp_json, skill_root=plugin_dir, expand_defaults=False
            )
            if config and "mcpServers" in config:
                logger.info(
                    "Loaded MCP config from %s with %d server(s)",
                    mcp_json,
                    len(config["mcpServers"]),
                )
            servers = config.get("mcpServers", {}) if isinstance(config, dict) else {}
            return coerce_mcp_config(servers)
        except Exception as e:
            logger.warning(f"Failed to load MCP config from {mcp_json}: {e}")
            return {}

    def load_hooks(self, plugin_dir: Path) -> HookConfig | None:
        """Load hooks configuration from ``hooks/hooks.json``."""
        hooks_json = plugin_dir / "hooks" / "hooks.json"
        if not hooks_json.exists():
            return None

        try:
            hook_config = HookConfig.load(path=hooks_json)
            # If hooks.json exists but is invalid, HookConfig.load() returns an
            # empty config and logs the validation error. Keep that distinct from
            # "file not present" (None).
            if hook_config.is_empty():
                logger.info(f"No hooks configured in {hooks_json}")
                return HookConfig()
            logger.info(f"Loaded hooks from {hooks_json}")
            return hook_config
        except Exception as e:
            logger.warning(f"Failed to load hooks from {hooks_json}: {e}")
            return None

    def load_agents(self, plugin_dir: Path) -> list[AgentDefinition]:
        """Load agent definitions from the ``agents/`` directory."""
        agents_dir = plugin_dir / "agents"
        if not agents_dir.is_dir():
            return []

        agents: list[AgentDefinition] = []
        for item in sorted(agents_dir.iterdir()):
            if item.suffix == ".md" and item.name.lower() != "readme.md":
                try:
                    agent = AgentDefinition.load(item)
                    agents.append(agent)
                    logger.debug(f"Loaded agent: {agent.name} from {item}")
                except Exception as e:
                    logger.warning(f"Failed to load agent from {item}: {e}")

        return agents

    def load_commands(self, plugin_dir: Path) -> list[CommandDefinition]:
        """Load command definitions from the ``commands/`` directory."""
        commands_dir = plugin_dir / "commands"
        if not commands_dir.is_dir():
            return []

        commands: list[CommandDefinition] = []
        for item in sorted(commands_dir.iterdir()):
            if item.suffix == ".md" and item.name.lower() != "readme.md":
                try:
                    command = CommandDefinition.load(item)
                    commands.append(command)
                    logger.debug(f"Loaded command: {command.name} from {item}")
                except Exception as e:
                    logger.warning(f"Failed to load command from {item}: {e}")

        return commands


# Registered formats, in detection-precedence order. Higher-precedence formats
# come first; the Claude Code format is last because it accepts any directory.
# The Agent Plugins format (root plugin.json, closed schema, mcp.json) will be
# inserted ahead of Claude Code here in a follow-up.
_FORMATS: list[type[PluginFormat]] = [ClaudeCodePluginFormat]


def detect_format(plugin_dir: Path) -> PluginFormat:
    """Select the plugin format for ``plugin_dir``.

    Precedence: the first registered format whose ``detect()`` returns True. The
    Claude Code format matches unconditionally, so this always resolves.
    """
    for fmt_cls in _FORMATS:
        if fmt_cls.detect(plugin_dir):
            logger.debug(f"Detected plugin format '{fmt_cls.name}' for {plugin_dir}")
            return fmt_cls()
    # Unreachable while ClaudeCodePluginFormat.detect() returns True, but keep an
    # explicit, actionable error rather than an implicit None if that changes.
    raise ValueError(f"No plugin format matched {plugin_dir}")


def _load_skills_from_skills_dir(skills_dir: Path) -> list[Skill]:
    """Load every skill under a plugin's ``skills/`` directory."""
    skills: list[Skill] = []
    for item in sorted(skills_dir.iterdir()):
        if item.is_dir():
            skill_md = find_skill_md(item)
            if skill_md:
                try:
                    # Skill.load() discovers resources, no need to do it again
                    skill = Skill.load(skill_md, skills_dir, strict=False)
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


def _load_root_skill(plugin_dir: Path, skill_md: Path) -> list[Skill]:
    """Load a single-skill plugin whose ``SKILL.md`` lives at the plugin root.

    For root skills, the plugin directory is the skill root, so .mcp.json at the
    plugin level is the same file that Skill.load() would try to load. We pass
    skip_mcp=True to avoid double-loading with different semantics (plugin-level
    uses expand_defaults=False for deferred secret expansion; skill-level would
    use expand_defaults=True and raise on validation errors).
    """
    try:
        # skip_mcp=True: Plugin-level MCP already loaded
        # Skill.load() discovers resources, no need to do it again
        skill = Skill.load(skill_md, plugin_dir, strict=False, skip_mcp=True)
        logger.debug(f"Loaded single-skill plugin: {skill.name} from {skill_md}")
        return [skill]
    except Exception as e:
        logger.warning(f"Failed to load root skill from {plugin_dir}: {e}")
        return []
