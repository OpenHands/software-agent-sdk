"""Type definitions for Marketplace module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from openhands.sdk.plugin.types import (
    HooksConfigDict,
    LspServersDict,
    McpServersDict,
    PluginAuthor,
    PluginManifest,
)


# Directories to check for marketplace manifest
MARKETPLACE_MANIFEST_DIRS = [".plugin", ".claude-plugin"]
MARKETPLACE_MANIFEST_FILE = "marketplace.json"


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


class MarketplaceEntry(BaseModel):
    """Base class for marketplace entries (plugins and skills).

    Both plugins and skills are pointers to directories:
    - Plugin directories contain: plugin.json, skills/, commands/, agents/, etc.
    - Skill directories contain: SKILL.md and optionally scripts/, references/, assets/

    Source is a string path (local path or GitHub URL).
    """

    name: str = Field(description="Identifier (kebab-case, no spaces)")
    source: str = Field(description="Path to directory (local path or GitHub URL)")
    description: str | None = Field(default=None, description="Brief description")
    version: str | None = Field(default=None, description="Version")
    author: PluginAuthor | None = Field(default=None, description="Author information")
    category: str | None = Field(default=None, description="Category for organization")
    homepage: str | None = Field(
        default=None, description="Homepage or documentation URL"
    )

    model_config = {"extra": "allow", "populate_by_name": True}

    @field_validator("author", mode="before")
    @classmethod
    def _parse_author(cls, v: Any) -> Any:
        if isinstance(v, str):
            return PluginAuthor.from_string(v)
        return v


class MarketplacePluginEntry(MarketplaceEntry):
    """Plugin entry in a marketplace.

    Extends MarketplaceEntry with Claude Code compatibility fields for
    inline plugin definitions (when strict=False).

    Plugins support both string sources and complex source objects
    (MarketplacePluginSource) for GitHub/git URLs with ref and path.
    """

    # Override source to allow complex source objects for plugins
    source: str | MarketplacePluginSource = Field(  # type: ignore[assignment]
        description="Path to plugin directory or source object for GitHub/git"
    )

    # Plugin-specific fields
    entry_command: str | None = Field(
        default=None,
        description=(
            "Default command to invoke when launching this plugin. "
            "Should match a command name from the commands/ directory."
        ),
    )

    # Claude Code compatibility fields
    strict: bool = Field(
        default=True,
        description="If True, plugin source must contain plugin.json. "
        "If False, marketplace entry defines the plugin inline.",
    )
    commands: str | list[str] | None = Field(default=None)
    agents: str | list[str] | None = Field(default=None)
    hooks: str | HooksConfigDict | None = Field(default=None)
    mcp_servers: McpServersDict | None = Field(default=None, alias="mcpServers")
    lsp_servers: LspServersDict | None = Field(default=None, alias="lspServers")

    # Additional metadata fields
    license: str | None = Field(default=None, description="SPDX license identifier")
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    repository: str | None = Field(
        default=None, description="Source code repository URL"
    )

    @field_validator("source", mode="before")
    @classmethod
    def _parse_source(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return MarketplacePluginSource.model_validate(v)
        return v

    def to_plugin_manifest(self) -> PluginManifest:
        """Convert to PluginManifest (for strict=False entries)."""
        return PluginManifest(
            name=self.name,
            version=self.version or "1.0.0",
            description=self.description or "",
            author=self.author,
            entry_command=self.entry_command,
        )


class MarketplaceMetadata(BaseModel):
    """Optional metadata for a marketplace."""

    description: str | None = Field(default=None)
    version: str | None = Field(default=None)

    model_config = {"extra": "allow", "populate_by_name": True}


class Marketplace(BaseModel):
    """A plugin marketplace that lists available plugins and skills.

    Follows the Claude Code marketplace structure for compatibility,
    with an additional `skills` field for standalone skill references.

    The marketplace.json file is located in `.plugin/` or `.claude-plugin/`
    directory at the root of the marketplace repository.

    Example:
    ```json
    {
        "name": "company-tools",
        "owner": {"name": "DevTools Team"},
        "plugins": [
            {"name": "formatter", "source": "./plugins/formatter"}
        ],
        "skills": [
            {"name": "github", "source": "./skills/github"}
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
    skills: list[MarketplaceEntry] = Field(
        default_factory=list, description="List of standalone skills"
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

        Returns:
            Tuple of (source, ref, subpath) where:
            - source: Resolved source string (path or URL)
            - ref: Branch, tag, or commit reference (None for local paths)
            - subpath: Subdirectory path within the repo (None if not specified)
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
                f"source type '{source.source}' is missing required field"
            )

        # Absolute paths or URLs - return as-is
        if source.startswith(("/", "~")) or "://" in source:
            return (source, None, None)

        # Relative path - resolve against marketplace path if known
        if self.path:
            source = str(Path(self.path) / source.lstrip("./"))

        return (source, None, None)
