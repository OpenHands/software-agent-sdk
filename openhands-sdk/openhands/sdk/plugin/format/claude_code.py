"""The Claude Code plugin format (OpenHands' original layout).

This is the concrete :class:`~openhands.sdk.plugin.format.base.PluginFormat`
strategy for the Claude-Code-style plugin directory. It is the universal
fallback: its :meth:`ClaudeCodePluginFormat.detect` accepts any directory,
inferring a manifest from the directory name when none is present.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.plugin.format.base import PluginFormat
from openhands.sdk.plugin.types import (
    CommandDefinition,
    PluginAuthor,
    PluginManifest,
)
from openhands.sdk.skills.utils import load_mcp_config
from openhands.sdk.subagent.schema import AgentDefinition


logger = get_logger(__name__)

# Directories the Claude Code layout checks for a (nested) manifest, in order.
PLUGIN_MANIFEST_DIRS = [".plugin", ".claude-plugin"]
PLUGIN_MANIFEST_FILE = "plugin.json"


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
