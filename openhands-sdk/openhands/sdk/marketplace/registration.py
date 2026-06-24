"""Marketplace registration model."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from pydantic import BaseModel, Field, field_validator


class MarketplaceRegistration(BaseModel):
    """Registration for a marketplace source used for plugin resolution."""

    name: str = Field(description="Identifier for this marketplace registration")
    source: str = Field(
        description="Marketplace source: 'github:owner/repo', git URL, or local path"
    )
    ref: str | None = Field(
        default=None,
        description="Optional branch, tag, or commit for git sources",
    )
    repo_path: str | None = Field(
        default=None,
        description=(
            "Subdirectory path within the git repository containing the marketplace. "
            "Only relevant for git sources."
        ),
    )
    auto_load: bool = Field(
        default=False,
        description="Whether to load all marketplace plugins at conversation start.",
    )

    @field_validator("repo_path")
    @classmethod
    def _validate_repo_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value:
            raise ValueError("repo_path must not be empty")
        if "\\" in value:
            raise ValueError("repo_path must use '/' separators")
        path = PurePosixPath(value)
        if PureWindowsPath(value).drive or path.is_absolute():
            raise ValueError("repo_path must be relative, not absolute")
        if ".." in path.parts:
            raise ValueError("repo_path cannot contain '..' path traversal")
        return value
