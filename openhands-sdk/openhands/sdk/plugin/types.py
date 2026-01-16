"""Type definitions for Plugin module."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field, field_validator, model_validator


# Directories to check for marketplace manifest
MARKETPLACE_MANIFEST_DIRS = [".plugin", ".claude-plugin"]
MARKETPLACE_MANIFEST_FILE = "marketplace.json"

# Type aliases for marketplace plugin entry configurations
# These provide better documentation than dict[str, Any] while remaining flexible

#: MCP server configuration dict. Keys are server names, values are server configs.
#: Each config should have 'command' (str), optional 'args' (list[str]), 'env'.
#: See https://gofastmcp.com/clients/client#configuration-format
type McpServersDict = dict[str, dict[str, Any]]

#: LSP server configuration dict. Keys are server names, values are server configs.
#: Each server config should have 'command' (str) and optional 'args' (list[str]),
#: 'extensionToLanguage' (dict mapping file extensions to language IDs).
#: See https://github.com/OpenHands/software-agent-sdk/issues/1745 for LSP support.
type LspServersDict = dict[str, dict[str, Any]]

#: Hooks configuration dict matching HookConfig.to_dict() structure.
#: Should have 'hooks' key with event types mapping to list of matchers.
#: See openhands.sdk.hooks.HookConfig for the full structure.
type HooksConfigDict = dict[str, Any]


class PluginAuthor(BaseModel):
    """Author information for a plugin."""

    name: str = Field(description="Author's name")
    email: str | None = Field(default=None, description="Author's email address")

    @classmethod
    def from_string(cls, author_str: str) -> PluginAuthor:
        """Parse author from string format 'Name <email>'."""
        if "<" in author_str and ">" in author_str:
            name = author_str.split("<")[0].strip()
            email = author_str.split("<")[1].split(">")[0].strip()
            return cls(name=name, email=email)
        return cls(name=author_str.strip())


class PluginManifest(BaseModel):
    """Plugin manifest from plugin.json."""

    name: str = Field(description="Plugin name")
    version: str = Field(default="1.0.0", description="Plugin version")
    description: str = Field(default="", description="Plugin description")
    author: PluginAuthor | None = Field(default=None, description="Plugin author")

    model_config = {"extra": "allow"}


def _extract_examples(description: str) -> list[str]:
    """Extract <example> tags from description for agent triggering."""
    pattern = r"<example>(.*?)</example>"
    matches = re.findall(pattern, description, re.DOTALL | re.IGNORECASE)
    return [m.strip() for m in matches if m.strip()]


class AgentDefinition(BaseModel):
    """Agent definition loaded from markdown file.

    Agents are specialized configurations that can be triggered based on
    user input patterns. They define custom system prompts and tool access.
    """

    name: str = Field(description="Agent name (from frontmatter or filename)")
    description: str = Field(default="", description="Agent description")
    model: str = Field(
        default="inherit", description="Model to use ('inherit' uses parent model)"
    )
    color: str | None = Field(default=None, description="Display color for the agent")
    tools: list[str] = Field(
        default_factory=list, description="List of allowed tools for this agent"
    )
    system_prompt: str = Field(default="", description="System prompt content")
    source: str | None = Field(
        default=None, description="Source file path for this agent"
    )
    # whenToUse examples extracted from description
    when_to_use_examples: list[str] = Field(
        default_factory=list,
        description="Examples of when to use this agent (for triggering)",
    )
    # Raw frontmatter for any additional fields
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata from frontmatter"
    )

    @classmethod
    def load(cls, agent_path: Path) -> AgentDefinition:
        """Load an agent definition from a markdown file.

        Agent markdown files have YAML frontmatter with:
        - name: Agent name
        - description: Description with optional <example> tags for triggering
        - model: Model to use (default: 'inherit')
        - color: Display color
        - tools: List of allowed tools

        The body of the markdown is the system prompt.

        Args:
            agent_path: Path to the agent markdown file.

        Returns:
            Loaded AgentDefinition instance.
        """
        with open(agent_path) as f:
            post = frontmatter.load(f)

        fm = post.metadata
        content = post.content.strip()

        # Extract frontmatter fields with proper type handling
        name = str(fm.get("name", agent_path.stem))
        description = str(fm.get("description", ""))
        model = str(fm.get("model", "inherit"))
        color_raw = fm.get("color")
        color: str | None = str(color_raw) if color_raw is not None else None
        tools_raw = fm.get("tools", [])

        # Ensure tools is a list of strings
        tools: list[str]
        if isinstance(tools_raw, str):
            tools = [tools_raw]
        elif isinstance(tools_raw, list):
            tools = [str(t) for t in tools_raw]
        else:
            tools = []

        # Extract whenToUse examples from description
        when_to_use_examples = _extract_examples(description)

        # Remove known fields from metadata to get extras
        known_fields = {"name", "description", "model", "color", "tools"}
        metadata = {k: v for k, v in fm.items() if k not in known_fields}

        return cls(
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


class CommandDefinition(BaseModel):
    """Command definition loaded from markdown file.

    Commands are slash commands that users can invoke directly.
    They define instructions for the agent to follow.
    """

    name: str = Field(description="Command name (from filename, e.g., 'review')")
    description: str = Field(default="", description="Command description")
    argument_hint: str | None = Field(
        default=None, description="Hint for command arguments"
    )
    allowed_tools: list[str] = Field(
        default_factory=list, description="List of allowed tools for this command"
    )
    content: str = Field(default="", description="Command instructions/content")
    source: str | None = Field(
        default=None, description="Source file path for this command"
    )
    # Raw frontmatter for any additional fields
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata from frontmatter"
    )

    @classmethod
    def load(cls, command_path: Path) -> CommandDefinition:
        """Load a command definition from a markdown file.

        Command markdown files have YAML frontmatter with:
        - description: Command description
        - argument-hint: Hint for command arguments (string or list)
        - allowed-tools: List of allowed tools

        The body of the markdown is the command instructions.

        Args:
            command_path: Path to the command markdown file.

        Returns:
            Loaded CommandDefinition instance.
        """
        with open(command_path) as f:
            post = frontmatter.load(f)

        # Extract frontmatter fields with proper type handling
        fm = post.metadata
        name = command_path.stem  # Command name from filename
        description = str(fm.get("description", ""))
        argument_hint_raw = fm.get("argument-hint") or fm.get("argumentHint")
        allowed_tools_raw = fm.get("allowed-tools") or fm.get("allowedTools") or []

        # Handle argument_hint as list (join with space) or string
        argument_hint: str | None
        if isinstance(argument_hint_raw, list):
            argument_hint = " ".join(str(h) for h in argument_hint_raw)
        elif argument_hint_raw is not None:
            argument_hint = str(argument_hint_raw)
        else:
            argument_hint = None

        # Ensure allowed_tools is a list of strings
        allowed_tools: list[str]
        if isinstance(allowed_tools_raw, str):
            allowed_tools = [allowed_tools_raw]
        elif isinstance(allowed_tools_raw, list):
            allowed_tools = [str(t) for t in allowed_tools_raw]
        else:
            allowed_tools = []

        # Remove known fields from metadata to get extras
        known_fields = {
            "description",
            "argument-hint",
            "argumentHint",
            "allowed-tools",
            "allowedTools",
        }
        metadata = {k: v for k, v in fm.items() if k not in known_fields}

        return cls(
            name=name,
            description=description,
            argument_hint=argument_hint,
            allowed_tools=allowed_tools,
            content=post.content.strip(),
            source=str(command_path),
            metadata=metadata,
        )


class MarketplaceOwner(BaseModel):
    """Owner information for a marketplace.

    The owner represents the maintainer or team responsible for the marketplace.
    """

    name: str = Field(description="Name of the maintainer or team")
    email: str | None = Field(
        default=None, description="Contact email for the maintainer"
    )


class MarketplacePluginSource(BaseModel):
    """Plugin source specification for non-local sources.

    Supports GitHub repositories and generic git URLs.
    """

    source: str = Field(description="Source type: 'github' or 'url'")
    repo: str | None = Field(
        default=None, description="GitHub repository in 'owner/repo' format"
    )
    url: str | None = Field(default=None, description="Git URL for 'url' source type")
    ref: str | None = Field(
        default=None, description="Branch, tag, or commit reference"
    )
    path: str | None = Field(
        default=None, description="Subdirectory path within the repository"
    )

    model_config = {"extra": "allow"}

    @model_validator(mode="after")
    def validate_source_fields(self) -> MarketplacePluginSource:
        """Validate that required fields are present based on source type."""
        if self.source == "github" and not self.repo:
            raise ValueError("GitHub source requires 'repo' field")
        if self.source == "url" and not self.url:
            raise ValueError("URL source requires 'url' field")
        return self


class MarketplacePluginEntry(BaseModel):
    """Plugin entry in a marketplace.

    Represents a single plugin available in the marketplace with its
    metadata and source location.

    This schema extends the core PluginManifest fields (name, version,
    description, author) with marketplace-specific fields (source, category,
    tags) and optional inline component definitions.

    The `license` and `keywords` fields align with the AgentSkills standard
    (https://agentskills.io/specification) used by Skill definitions.

    Related schemas:
        - PluginManifest: Core plugin metadata (plugin.json)
        - Skill: Individual skill definitions with license, description
        - Plugin: Loaded plugin with skills, commands, agents, hooks
    """

    # Core fields (shared with PluginManifest)
    name: str = Field(
        description="Plugin identifier (kebab-case, no spaces). "
        "Users see this when installing: /plugin install <name>@marketplace"
    )
    version: str | None = Field(default=None, description="Plugin version")
    description: str | None = Field(
        default=None, description="Brief plugin description"
    )
    author: PluginAuthor | None = Field(
        default=None, description="Plugin author information"
    )

    # Marketplace-specific: source location
    source: str | MarketplacePluginSource = Field(
        description="Where to fetch the plugin from. Can be a relative path string "
        "(e.g., './plugins/my-plugin') or a source object for GitHub/git URLs"
    )

    # Discovery and categorization (aligns with Skill.license for compatibility)
    license: str | None = Field(
        default=None,
        description="SPDX license identifier (e.g., MIT, Apache-2.0). "
        "Aligns with AgentSkills standard used by Skill definitions.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Tags for plugin discovery and categorization. "
        "Aligns with AgentSkills standard used by Skill definitions.",
    )
    category: str | None = Field(
        default=None, description="Plugin category for organization"
    )
    tags: list[str] = Field(default_factory=list, description="Tags for searchability")

    # Repository/project links
    homepage: str | None = Field(
        default=None, description="Plugin homepage or documentation URL"
    )
    repository: str | None = Field(
        default=None, description="Source code repository URL"
    )

    # Marketplace behavior control
    strict: bool = Field(
        default=True,
        description="If True, plugin source must contain plugin.json. "
        "If False, marketplace entry defines everything about the plugin.",
    )

    # Inline plugin component definitions (when strict=False)
    # These fields are part of the marketplace schema for future use.
    # Currently, Plugin.load() reads these from the plugin directory itself.
    # TODO: Support loading inline definitions from marketplace entries.
    commands: str | list[str] | None = Field(
        default=None,
        description="Custom paths to command files or directories. "
        "Loaded as CommandDefinition objects by Plugin.",
    )
    agents: str | list[str] | None = Field(
        default=None,
        description="Custom paths to agent files. "
        "Loaded as AgentDefinition objects by Plugin.",
    )
    hooks: str | HooksConfigDict | None = Field(
        default=None,
        description="Hooks configuration - either a path to hooks.json file (str) "
        "or inline configuration dict. Loaded as HookConfig by Plugin. "
        "See openhands.sdk.hooks.HookConfig for the expected structure.",
    )
    mcp_servers: McpServersDict | None = Field(
        default=None,
        alias="mcpServers",
        description="MCP server configurations keyed by server name. "
        "Each server config should have 'command' and optional 'args', 'env'. "
        "Corresponds to Plugin.mcp_config loaded from .mcp.json. "
        "See https://gofastmcp.com/clients/client#configuration-format",
    )
    lsp_servers: LspServersDict | None = Field(
        default=None,
        alias="lspServers",
        description="LSP server configurations keyed by server name. "
        "Each server config should have 'command' and optional 'args'.",
    )

    model_config = {"extra": "allow", "populate_by_name": True}

    @field_validator("author", mode="before")
    @classmethod
    def _parse_author(cls, v: Any) -> Any:
        """Parse author from string format 'Name <email>' if needed."""
        if isinstance(v, str):
            return PluginAuthor.from_string(v)
        return v

    @field_validator("source", mode="before")
    @classmethod
    def _parse_source(cls, v: Any) -> Any:
        """Parse source dict to MarketplacePluginSource if needed."""
        if isinstance(v, dict):
            return MarketplacePluginSource.model_validate(v)
        return v

    def to_plugin_manifest(self) -> PluginManifest:
        """Convert marketplace entry to a PluginManifest.

        Useful when strict=False and the marketplace entry defines the
        plugin metadata directly without a separate plugin.json file.

        Returns:
            PluginManifest with the core fields from this entry.
        """
        return PluginManifest(
            name=self.name,
            version=self.version or "1.0.0",
            description=self.description or "",
            author=self.author,
        )


class MarketplaceMetadata(BaseModel):
    """Optional metadata for a marketplace."""

    description: str | None = Field(
        default=None, description="Brief marketplace description"
    )
    version: str | None = Field(default=None, description="Marketplace version")
    plugin_root: str | None = Field(
        default=None,
        alias="pluginRoot",
        description="Base directory prepended to relative plugin source paths. "
        "E.g., './plugins' allows writing 'source: formatter' "
        "instead of 'source: ./plugins/formatter'",
    )

    model_config = {"extra": "allow", "populate_by_name": True}


class Marketplace(BaseModel):
    """A plugin marketplace that lists available plugins.

    Marketplaces follow the Claude Code marketplace structure for compatibility.
    The marketplace.json file is located in `.plugin/` or `.claude-plugin/`
    directory at the root of the marketplace repository.

    Example marketplace.json:
    ```json
    {
        "name": "company-tools",
        "owner": {
            "name": "DevTools Team",
            "email": "devtools@example.com"
        },
        "description": "Internal development tools",
        "metadata": {
            "version": "1.0.0",
            "pluginRoot": "./plugins"
        },
        "plugins": [
            {
                "name": "code-formatter",
                "source": "./plugins/formatter",
                "description": "Automatic code formatting"
            },
            {
                "name": "deployment-tools",
                "source": {
                    "source": "github",
                    "repo": "company/deploy-plugin"
                }
            }
        ]
    }
    ```
    """

    name: str = Field(
        description="Marketplace identifier (kebab-case, no spaces). "
        "Users see this when installing plugins: /plugin install tool@<marketplace>"
    )
    owner: MarketplaceOwner = Field(description="Marketplace maintainer information")
    description: str | None = Field(
        default=None,
        description="Brief marketplace description. Can also be in metadata.",
    )
    plugins: list[MarketplacePluginEntry] = Field(
        default_factory=list, description="List of available plugins"
    )
    metadata: MarketplaceMetadata | None = Field(
        default=None, description="Optional marketplace metadata"
    )
    path: str | None = Field(
        default=None,
        description="Path to the marketplace directory (set after loading)",
    )

    model_config = {"extra": "allow"}

    @classmethod
    def load(cls, marketplace_path: str | Path) -> Marketplace:
        """Load a marketplace from a directory.

        Looks for marketplace.json in .plugin/ or .claude-plugin/ directories.

        Args:
            marketplace_path: Path to the marketplace directory.

        Returns:
            Loaded Marketplace instance.

        Raises:
            FileNotFoundError: If the marketplace directory or manifest doesn't exist.
            ValueError: If the marketplace manifest is invalid.
        """
        marketplace_dir = Path(marketplace_path).resolve()
        if not marketplace_dir.is_dir():
            raise FileNotFoundError(
                f"Marketplace directory not found: {marketplace_dir}"
            )

        # Find manifest file
        manifest_path = None
        for manifest_dir in MARKETPLACE_MANIFEST_DIRS:
            candidate = marketplace_dir / manifest_dir / MARKETPLACE_MANIFEST_FILE
            if candidate.exists():
                manifest_path = candidate
                break

        if manifest_path is None:
            dirs = " or ".join(MARKETPLACE_MANIFEST_DIRS)
            raise FileNotFoundError(
                f"Marketplace manifest not found. "
                f"Expected {MARKETPLACE_MANIFEST_FILE} in {dirs} "
                f"directory under {marketplace_dir}"
            )

        try:
            with open(manifest_path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {manifest_path}: {e}") from e

        return cls.model_validate({**data, "path": str(marketplace_dir)})

    def get_plugin(self, name: str) -> MarketplacePluginEntry | None:
        """Get a plugin entry by name.

        Args:
            name: Plugin name to look up.

        Returns:
            MarketplacePluginEntry if found, None otherwise.
        """
        for plugin in self.plugins:
            if plugin.name == name:
                return plugin
        return None

    def resolve_plugin_source(
        self, plugin: MarketplacePluginEntry
    ) -> tuple[str, str | None, str | None]:
        """Resolve a plugin's source to a full path or URL.

        Handles relative paths and plugin_root from metadata.

        Args:
            plugin: Plugin entry to resolve source for.

        Returns:
            Tuple of (source, ref, subpath) where:
            - source: Resolved source string (path or URL)
            - ref: Branch, tag, or commit reference (None for local paths)
            - subpath: Subdirectory path within the repo (None if not specified)

        Raises:
            ValueError: If source object is invalid.
        """
        source = plugin.source

        # Handle complex source objects (GitHub, git URLs)
        if isinstance(source, MarketplacePluginSource):
            if source.source == "github" and source.repo:
                return (f"github:{source.repo}", source.ref, source.path)
            if source.source == "url" and source.url:
                return (source.url, source.ref, source.path)
            raise ValueError(
                f"Invalid plugin source for '{plugin.name}': "
                f"source type '{source.source}' is missing required field. "
                f"'github' sources require 'repo', 'url' sources require 'url'"
            )

        # Source is a string path - check if it's absolute or a URL
        if source.startswith(("/", "~")) or "://" in source:
            return (source, None, None)

        # Relative path: apply plugin_root if configured
        if self.metadata and self.metadata.plugin_root:
            plugin_root = self.metadata.plugin_root.rstrip("/")
            source = f"{plugin_root}/{source.lstrip('./')}"

        # Resolve relative paths to absolute if we know the marketplace path
        if self.path and not source.startswith(("/", "~")):
            source = str(Path(self.path) / source.lstrip("./"))

        return (source, None, None)
