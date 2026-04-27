"""Type definitions for Plugin module."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import frontmatter
from pydantic import BaseModel, Field, field_validator


class PluginSource(BaseModel):
    """Specification for a plugin to load.

    This model describes where to find a plugin and is used by load_plugins()
    to fetch and load plugins from various sources.

    Examples:
        >>> # GitHub repository
        >>> PluginSource(source="github:owner/repo", ref="v1.0.0")

        >>> # Plugin from monorepo subdirectory
        >>> PluginSource(
        ...     source="github:owner/monorepo",
        ...     repo_path="plugins/my-plugin"
        ... )

        >>> # Local path
        >>> PluginSource(source="/path/to/plugin")
    """

    source: str = Field(
        description="Plugin source: 'github:owner/repo', any git URL, or local path"
    )
    ref: str | None = Field(
        default=None,
        description="Optional branch, tag, or commit (only for git sources)",
    )
    repo_path: str | None = Field(
        default=None,
        description=(
            "Subdirectory path within the git repository "
            "(e.g., 'plugins/my-plugin' for monorepos). "
            "Only relevant for git sources, not local paths."
        ),
    )

    @field_validator("repo_path")
    @classmethod
    def validate_repo_path(cls, v: str | None) -> str | None:
        """Validate repo_path is a safe relative path within the repository."""
        if v is None:
            return v
        # Must be relative (no absolute paths)
        if v.startswith("/"):
            raise ValueError("repo_path must be relative, not absolute")
        # No parent directory traversal
        if ".." in Path(v).parts:
            raise ValueError(
                "repo_path cannot contain '..' (parent directory traversal)"
            )
        return v

    @property
    def source_url(self) -> str | None:
        """Convert the plugin source to a canonical URL.

        Converts the 'github:' convenience prefix to a full URL.
        For sources that are already URLs, returns them directly.
        Local paths return None (not portable).

        Returns:
            URL string, or None for local paths.

        Examples:
            >>> PluginSource(source="github:owner/repo").source_url
            'https://github.com/owner/repo'

            >>> PluginSource(source="github:owner/repo", ref="v1.0").source_url
            'https://github.com/owner/repo/tree/v1.0'

            >>> PluginSource(source="https://github.com/owner/repo").source_url
            'https://github.com/owner/repo'

            >>> PluginSource(source="/local/path").source_url
            None
        """
        # Handle github: shorthand - the only convenience prefix we support
        if self.source.startswith("github:"):
            repo_part = self.source[7:]  # Remove 'github:' prefix
            base_url = f"https://github.com/{repo_part}"
            if self.ref or self.repo_path:
                ref = self.ref or "main"
                if self.repo_path:
                    return f"{base_url}/tree/{ref}/{self.repo_path}"
                return f"{base_url}/tree/{ref}"
            return base_url

        # Already a URL - return as-is
        if self.source.startswith(("https://", "http://", "git@", "git://")):
            return self.source

        # Local paths - not portable, return None
        return None


class ResolvedPluginSource(BaseModel):
    """A plugin source with resolved ref (pinned to commit SHA).

    Used for persistence to ensure deterministic behavior across pause/resume.
    When a conversation is resumed, the resolved ref ensures we get exactly
    the same plugin version that was used when the conversation started.

    The resolved_ref is the actual commit SHA that was fetched, even if the
    original ref was a branch name like 'main'. This prevents drift when
    branches are updated between pause and resume.
    """

    source: str = Field(
        description="Plugin source: 'github:owner/repo', any git URL, or local path"
    )
    resolved_ref: str | None = Field(
        default=None,
        description=(
            "Resolved commit SHA (for git sources). None for local paths. "
            "This is the actual commit that was checked out, even if the "
            "original ref was a branch name."
        ),
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within the git repository",
    )
    original_ref: str | None = Field(
        default=None,
        description="Original ref from PluginSource (for debugging/display)",
    )

    @classmethod
    def from_plugin_source(
        cls, plugin_source: PluginSource, resolved_ref: str | None
    ) -> ResolvedPluginSource:
        """Create a ResolvedPluginSource from a PluginSource and resolved ref."""
        return cls(
            source=plugin_source.source,
            resolved_ref=resolved_ref,
            repo_path=plugin_source.repo_path,
            original_ref=plugin_source.ref,
        )

    def to_plugin_source(self) -> PluginSource:
        """Convert back to PluginSource using the resolved ref.

        When loading from persistence, use the resolved_ref to ensure we get
        the exact same version that was originally fetched.
        """
        return PluginSource(
            source=self.source,
            ref=self.resolved_ref,  # Use resolved SHA, not original ref
            repo_path=self.repo_path,
        )


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


if TYPE_CHECKING:
    from openhands.sdk.skills.skill import Skill


class PluginAuthor(BaseModel):
    """Author information for a plugin."""

    name: str = Field(description="Author's name")
    email: str | None = Field(default=None, description="Author's email address")
    url: str | None = Field(
        default=None, description="Author's URL (e.g., GitHub profile)"
    )

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
    entry_command: str | None = Field(
        default=None,
        description=(
            "Default command to invoke when launching this plugin. "
            "Should match a command name from the commands/ directory. "
            "Example: 'now' for a command defined in commands/now.md"
        ),
    )

    model_config = {"extra": "allow"}


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
        with open(command_path, encoding="utf-8") as f:
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

    def to_skill(self, plugin_name: str) -> Skill:
        """Convert this command to a keyword-triggered Skill.

        Creates a Skill with a KeywordTrigger using the Claude Code namespacing
        format: /<plugin-name>:<command-name>

        Args:
            plugin_name: The name of the plugin this command belongs to.

        Returns:
            A Skill object with the command content and a KeywordTrigger.

        Example:
            For a plugin "city-weather" with command "now":
            - Trigger keyword: "/city-weather:now"
            - When user types "/city-weather:now Tokyo", the skill activates
        """
        from openhands.sdk.skills.skill import Skill
        from openhands.sdk.skills.trigger import KeywordTrigger

        # Build the trigger keyword in Claude Code namespace format
        trigger_keyword = f"/{plugin_name}:{self.name}"

        # Build skill content with $ARGUMENTS placeholder context
        content_parts = []
        if self.description:
            content_parts.append(f"## {self.name}\n\n{self.description}\n")

        if self.argument_hint:
            content_parts.append(
                f"**Arguments**: `$ARGUMENTS` - {self.argument_hint}\n"
            )

        if self.content:
            content_parts.append(f"\n{self.content}")

        skill_content = "\n".join(content_parts).strip()

        return Skill(
            name=f"{plugin_name}:{self.name}",
            content=skill_content,
            description=self.description or f"Command {self.name} from {plugin_name}",
            trigger=KeywordTrigger(keywords=[trigger_keyword]),
            source=self.source,
            allowed_tools=self.allowed_tools if self.allowed_tools else None,
        )


# =============================================================================
# Deprecated marketplace classes - moved to openhands.sdk.marketplace
# =============================================================================
# These are re-exported here for backward compatibility. Import from
# openhands.sdk.marketplace instead.

# Names of deprecated marketplace exports
_DEPRECATED_MARKETPLACE_NAMES = frozenset(
    {
        "MARKETPLACE_MANIFEST_DIRS",
        "MARKETPLACE_MANIFEST_FILE",
        "Marketplace",
        "MarketplaceEntry",
        "MarketplaceMetadata",
        "MarketplaceOwner",
        "MarketplacePluginEntry",
        "MarketplacePluginSource",
    }
)


def __getattr__(name: str) -> Any:
    """Provide deprecated marketplace names with warnings via lazy import."""
    if name in _DEPRECATED_MARKETPLACE_NAMES:
        from openhands.sdk.utils.deprecation import warn_deprecated

        warn_deprecated(
            f"Importing {name} from openhands.sdk.plugin.types",
            deprecated_in="1.16.0",
            removed_in="1.19.0",
            details="Import from openhands.sdk.marketplace instead.",
            stacklevel=3,
        )
        # Lazy import to avoid circular dependency
        import openhands.sdk.marketplace.types as marketplace_types

        return getattr(marketplace_types, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
