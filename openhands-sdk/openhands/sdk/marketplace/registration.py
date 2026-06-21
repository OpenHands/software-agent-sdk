"""Marketplace registration model."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator


_WINDOWS_DRIVE_PATH = re.compile(r"^[a-zA-Z]:[\\/]")


def _validate_repo_path(value: str | None) -> str | None:
    if value is None:
        return None
    if not value:
        raise ValueError("repo_path must not be empty")
    if "\\" in value:
        raise ValueError("repo_path must use '/' separators")
    if _WINDOWS_DRIVE_PATH.match(value) or PurePosixPath(value).is_absolute():
        raise ValueError("repo_path must be relative, not absolute")
    if ".." in PurePosixPath(value).parts:
        raise ValueError("repo_path cannot contain '..' path traversal")
    return value


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
    auto_load: Literal["all"] | None = Field(
        default=None,
        description=(
            "Auto-load behavior for this marketplace. Use 'all' to load all "
            "plugins at conversation start; None registers for resolution only."
        ),
    )

    @field_validator("repo_path")
    @classmethod
    def _validate_repo_path(cls, value: str | None) -> str | None:
        return _validate_repo_path(value)
