"""``PluginFormat`` strategy base class and shared component helpers.

A *plugin format* owns everything that is specific to how a plugin is laid out
on disk: where its manifest lives and how it is validated, which file its MCP
config is read from and how variables in it are expanded, and how its client
extensions (commands / agents / hooks) are located. Each format's job is to read
a plugin directory and produce a normalized :class:`~openhands.sdk.plugin.Plugin`.

Everything *downstream* of a loaded plugin (merging skills into an agent context,
merging MCP servers, concatenating hooks) is format-agnostic and lives on
``Plugin`` itself, so adding a new format never touches the merge/apply path.

Concrete strategies live in their own modules (e.g. ``claude_code.py``); this
module holds only the abstract contract and the discovery logic shared by all
formats. See the ``openhands.sdk.plugin.format`` package docstring for the
recipe to add a new format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.plugin.types import CommandDefinition, PluginManifest
from openhands.sdk.skills.skill import Skill
from openhands.sdk.skills.utils import find_skill_md
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.utils.path import to_posix_path


if TYPE_CHECKING:
    from openhands.sdk.plugin.plugin import Plugin

logger = get_logger(__name__)


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

        ``plugin_dir`` is used as-is (it becomes the loaded ``Plugin.path``);
        this method does not resolve it. ``Plugin.load()`` resolves the path
        before dispatching here, so direct callers of
        ``detect_format(path).load(path)`` should pass an already-resolved path
        if they want ``Plugin.path`` fully resolved.
        """
        # Imported lazily to avoid a module-level import cycle with plugin.py,
        # which imports the format package for detect_format().
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
