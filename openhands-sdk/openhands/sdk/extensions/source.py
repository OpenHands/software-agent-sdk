"""Extension source specification types.

These types define where to fetch an extension (plugin, skill, etc.) from,
supporting GitHub shorthand, git URLs, and local paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, field_validator


class ExtensionSource(BaseModel):
    """Specification for where to fetch an extension.

    Supports multiple source formats:
    - GitHub shorthand: "github:owner/repo"
    - Git URLs: "https://github.com/owner/repo.git", "git@github.com:owner/repo.git"
    - Local paths: "/path/to/extension", "./relative/path"

    Examples:
        >>> ExtensionSource(source="github:owner/repo", ref="v1.0.0")
        >>> ExtensionSource(
        ...     source="github:owner/monorepo",
        ...     repo_path="extensions/my-ext",
        ... )
        >>> ExtensionSource(source="/path/to/extension")
    """

    source: str = Field(
        description="Extension source: 'github:owner/repo', any git URL, or local path"
    )
    ref: str | None = Field(
        default=None,
        description="Optional branch, tag, or commit (only for git sources)",
    )
    repo_path: str | None = Field(
        default=None,
        description=(
            "Subdirectory path within the git repository "
            "(e.g., 'extensions/my-ext' for monorepos). "
            "Only relevant for git sources, not local paths."
        ),
    )

    @field_validator("repo_path")
    @classmethod
    def validate_repo_path(cls, v: str | None) -> str | None:
        """Validate repo_path is a safe relative path within the repository."""
        if v is None:
            return v
        if v.startswith("/"):
            raise ValueError("repo_path must be relative, not absolute")
        if ".." in Path(v).parts:
            raise ValueError(
                "repo_path cannot contain '..' (parent directory traversal)"
            )
        return v

    @property
    def source_url(self) -> str | None:
        """Convert the extension source to a canonical URL.

        Converts the 'github:' convenience prefix to a full URL.
        For sources that are already URLs, returns them directly.
        Local paths return None (not portable).

        Returns:
            URL string, or None for local paths.

        Examples:
            >>> ExtensionSource(source="github:owner/repo").source_url
            'https://github.com/owner/repo'

            >>> ExtensionSource(source="github:owner/repo", ref="v1.0").source_url
            'https://github.com/owner/repo/tree/v1.0'

            >>> ExtensionSource(source="https://github.com/owner/repo").source_url
            'https://github.com/owner/repo'

            >>> ExtensionSource(source="/local/path").source_url
            None
        """
        if self.source.startswith("github:"):
            repo_part = self.source[7:]  # Remove 'github:' prefix
            base_url = f"https://github.com/{repo_part}"
            if self.ref or self.repo_path:
                ref = self.ref or "main"
                if self.repo_path:
                    return f"{base_url}/tree/{ref}/{self.repo_path}"
                return f"{base_url}/tree/{ref}"
            return base_url

        if self.source.startswith(("https://", "http://", "git@", "git://")):
            return self.source

        return None


class ResolvedExtensionSource(BaseModel):
    """An extension source with resolved ref (pinned to commit SHA).

    Used for persistence to ensure deterministic behavior across pause/resume.
    When a conversation is resumed, the resolved ref ensures we get exactly
    the same extension version that was used when the conversation started.

    The resolved_ref is the actual commit SHA that was fetched, even if the
    original ref was a branch name like 'main'. This prevents drift when
    branches are updated between pause and resume.
    """

    source: str = Field(
        description="Extension source: 'github:owner/repo', any git URL, or local path"
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
        description="Original ref from ExtensionSource (for debugging/display)",
    )

    @classmethod
    def from_source(
        cls, extension_source: ExtensionSource, resolved_ref: str | None
    ) -> Self:
        """Create a ResolvedExtensionSource from an ExtensionSource and resolved ref."""
        return cls(
            source=extension_source.source,
            resolved_ref=resolved_ref,
            repo_path=extension_source.repo_path,
            original_ref=extension_source.ref,
        )

    def to_source(self) -> ExtensionSource:
        """Convert back to ExtensionSource using the resolved ref.

        When loading from persistence, use the resolved_ref to ensure we get
        the exact same version that was originally fetched.
        """
        return ExtensionSource(
            source=self.source,
            ref=self.resolved_ref,  # Use resolved SHA, not original ref
            repo_path=self.repo_path,
        )
