"""Extension catalog entry types.

These types define entries in extension catalogs (marketplaces) that list
available extensions with their metadata and source locations.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, Field, field_validator


class ExtensionAuthor(BaseModel):
    """Author information for an extension."""

    name: str = Field(description="Author's name")
    email: str | None = Field(default=None, description="Author's email address")
    url: str | None = Field(
        default=None, description="Author's URL (e.g., GitHub profile)"
    )

    @classmethod
    def from_string(cls, author_str: str) -> Self:
        """Parse author from string format 'Name <email>'.

        Examples:
            >>> ExtensionAuthor.from_string("John Doe <john@example.com>")
            ExtensionAuthor(name='John Doe', email='john@example.com', url=None)

            >>> ExtensionAuthor.from_string("Jane Doe")
            ExtensionAuthor(name='Jane Doe', email=None, url=None)
        """
        if "<" in author_str and ">" in author_str:
            name = author_str.split("<")[0].strip()
            email = author_str.split("<")[1].split(">")[0].strip()
            return cls(name=name, email=email)
        return cls(name=author_str.strip())


class ExtensionCatalogEntry(BaseModel):
    """Entry in an extension catalog (marketplace).

    This is the base type for catalog entries that point to extensions
    (plugins, skills, etc.) with their metadata and source locations.

    Source is a string path that can be:
    - Local path: "./path/to/extension", "/absolute/path"
    - GitHub URL: "https://github.com/owner/repo/tree/branch/path"
    """

    name: str = Field(description="Identifier (kebab-case, no spaces)")
    source: str = Field(description="Path to extension directory (local or GitHub URL)")
    description: str | None = Field(default=None, description="Brief description")
    version: str | None = Field(default=None, description="Version")
    author: ExtensionAuthor | None = Field(default=None, description="Author info")
    category: str | None = Field(default=None, description="Category for organization")
    homepage: str | None = Field(
        default=None, description="Homepage or documentation URL"
    )

    model_config = {"extra": "allow", "populate_by_name": True}

    @field_validator("author", mode="before")
    @classmethod
    def _parse_author(cls, v: Any) -> Any:
        """Parse author from string if needed."""
        if isinstance(v, str):
            return ExtensionAuthor.from_string(v)
        return v
